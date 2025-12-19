#!/usr/bin/python3
import argparse
import re
import sys
import time
import paramiko
import subprocess
import os
import json
import concurrent.futures

from sh import python3
from ablestack import *

json_file_path = pluginpath + "/tools/properties/cluster.json"
def openClusterJson():
    try:
        with open(json_file_path, 'r') as json_file:
            ret = json.load(json_file)
    except Exception as e:
        ret = createReturn(code=500, val='cluster.json read error')
        print ('EXCEPTION : ',e)

    return ret

json_data = openClusterJson()

def parse_size(size_str):
    # 단위 변환 로직 (t → TB, g → GB, m → MB)
    import re
    match = re.match(r"[<>]?([\d.]+)([a-zA-Z]*)", size_str)
    if match:
        number, unit = match.groups()
        unit_mapping = {"t": "TB", "g": "GB", "m": "MB"}
        unit = unit_mapping.get(unit.lower(), unit)
        return f"{float(number):.2f}{unit}"
    return size_str

def run_command(command, ssh_client=None, ignore_errors=False, suppress_errors=True):
    """Run a shell command and return its output. Suppress or handle errors as specified."""
    try:
        if ssh_client:
            stdin, stdout, stderr = ssh_client.exec_command(command)
            stdout_str = stdout.read().decode()
            stderr_str = stderr.read().decode()

            # Suppress errors if specified
            if stderr_str and not suppress_errors:
                print(f"Error running command: {command}")
                print(stderr_str)
                if not ignore_errors:
                    raise Exception(f"Command failed: {command}")
            return stdout_str

        else:
            process = subprocess.Popen(
                command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            stdout, stderr = process.communicate()
            stdout_str = stdout.decode()
            stderr_str = stderr.decode()

            # Suppress errors if specified
            if process.returncode and not suppress_errors:
                print(f"Error running command: {command}")
                print(stderr_str)
                if not ignore_errors:
                    raise Exception(f"Command failed: {command}")

            # Return only stdout
            return stdout_str

    except Exception as e:
        if not ignore_errors:
            print(f"Error running command: {command}: {e}")
            raise

def connect_to_host(ip):
    """Establish an SSH connection to the host."""
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    private_key_path = os.getenv('SSH_PRIVATE_KEY_PATH', '/root/.ssh/id_rsa')
    ssh.connect(ip, username='root', key_filename=private_key_path)
    return ssh


def create_clvm(disks):
    try:
        # Prepare a list to store disk devices
        max_num = 0  # 가장 큰 vg_clvm 번호를 추적

        # /etc/hosts 파일에서 클러스터 노드 IP 가져오기
        result = subprocess.run(["grep", "ablecube", "/etc/hosts"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, check=True)
        lines = result.stdout.strip().split("\n")
        list_ips = [line.split()[0] for line in lines if line.strip()]

        # 기존 vg_clvm 볼륨 그룹 확인
        result = subprocess.run(["vgs", "-o", "vg_name", "--reportformat", "json"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        output = json.loads(result.stdout)

        # 기존 vg_clvm 번호 중 가장 큰 값을 찾음
        for vg in output["report"][0]["vg"]:
            vgs_name = vg["vg_name"]
            if "vg_clvm" in vgs_name:
                suffix = vgs_name.replace("vg_clvm", "")
                if suffix.isdigit():
                    num = int(suffix)
                    if num > max_num:
                        max_num = num

        # 새로운 vg_clvm 번호는 기존 최대값 + 1부터 시작
        next_num = max_num + 1

        # 디스크마다 물리 볼륨 및 볼륨 그룹 생성
        for disk in disks:
            # 디스크 이름 추출
            name = disk.split('/')[-1]

            # 볼륨 그룹 이름 생성
            vg_name = f"vg_clvm{next_num:02d}"  # 두 자리 형식으로 포맷
            multipath_check = os.popen("multipath -l -v 1").read().strip()
            if multipath_check != "":
                # 디스크에 파티션 생성 및 LVM 설정
                run_command(f"parted -s {disk} mklabel gpt mkpart {name} 0% 100% set 1 lvm on")
                partition = disk.replace("dm-uuid-mpath-","dm-uuid-part1-mpath-")
                run_command(f"pvcreate -y {partition}")
                run_command(f"vgcreate {vg_name} {partition}")

            # 클러스터의 모든 노드에서 LVM 정보 갱신
            for ip in list_ips:
                ssh_client = connect_to_host(ip)
                multipath_check = os.popen("multipath -l -v 1").read().strip()
                if multipath_check != "":
                    run_command(f"partprobe {disk}", ssh_client, ignore_errors=True)
                    run_command(f"lvmdevices --adddev {partition}", ssh_client, ignore_errors=True)
                else:
                    single_disk_arr = run_command("lsblk -r -n -o NAME,TYPE -d | grep -v rom | awk '{print $1}'", ssh_client).split()
                    for single_disk in single_disk_arr:
                        single_partition = f"{single_disk}1"
                        run_command(f"partprobe /dev/{single_disk}", ssh_client, ignore_errors=True)
                        run_command(f"lvmdevices --adddev /dev/{single_partition}", ssh_client, ignore_errors=True)
                ssh_client.close()

            next_num += 1  # 다음 볼륨 그룹 번호 증가

        # 성공 응답 반환
        ret = createReturn(code=200, val="Create CLVM Disk Success")
        return print(json.dumps(json.loads(ret), indent=4))

    except Exception as e:
        # 에러 발생 시 실패 응답 반환
        ret = createReturn(code=500, val=f"Create CLVM Disk Failure: {str(e)}")
        return print(json.dumps(json.loads(ret), indent=4))


def list_clvm():
    try:
        # pvs 명령 실행
        pvs_result = subprocess.run(
            ["pvs", "-o", "vg_name,pv_name,pv_size", "--reportformat", "json"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )

        # lsblk 명령 실행
        lsblk_result = subprocess.run(
            ["lsblk", "-o", "name,wwn", "--json"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )

        # multipathd 상태 확인
        mpath_status = os.popen("systemctl is-active multipathd").read().strip()

        # JSON 파싱
        pvs_output = json.loads(pvs_result.stdout)
        lsblk_output = json.loads(lsblk_result.stdout)

        # lsblk 데이터 맵핑
        lsblk_map = {}
        for dev in lsblk_output.get("blockdevices", []):
            if mpath_status == "active" and "children" in dev:
                for child in dev["children"]:
                    lsblk_map[child["name"]] = dev.get("wwn", "N/A")
            else:
                lsblk_map[dev["name"]] = dev.get("wwn", "N/A")

        # CLVM 필터링
        clvm_pvs = []
        for pv in pvs_output["report"][0]["pv"]:
            vg_name = pv.get("vg_name", "")
            if "vg_clvm" in vg_name:
                pv_name = pv.get("pv_name", "")
                real_path = os.path.realpath(pv_name)
                dm_name = os.path.basename(real_path)
                by_id_path = '/dev/disk/by-id'

                if mpath_status == "active":
                    for entry in os.listdir(by_id_path):
                        if entry.startswith("dm-uuid-part1-mpath"):
                            full_path = os.path.join(by_id_path, entry)
                            if os.path.islink(full_path):
                                resolved = os.path.realpath(full_path)
                                if os.path.basename(resolved) == dm_name:
                                    disk_id = entry
                                    break  # 하나만 필요하므로 종료
                else:
                    for entry in os.listdir(by_id_path):
                        full_path = os.path.join(by_id_path, entry)
                        if os.path.islink(full_path):
                            resolved = os.path.realpath(full_path)
                            if os.path.basename(resolved) == dm_name:
                                disk_id = entry
                                break  # 하나만 필요하므로 종료

                pv_size = parse_size(pv.get("pv_size", "0"))
                disk_name = os.path.basename(pv_name.split("/")[-1].split("1")[0])
                wwn = lsblk_map.get(disk_name, "N/A")

                clvm_pvs.append({
                    "vg_name": vg_name,
                    "pv_name": pv_name,
                    "pv_size": pv_size,
                    "wwn": wwn,
                    "disk_id": "/dev/disk/by-id/" + disk_id
                })
        clvm_pvs.sort(key=lambda x: int(x["vg_name"].replace("vg_clvm", "")))
        # 결과 반환
        ret = createReturn(code=200, val=clvm_pvs)
        return print(json.dumps(json.loads(ret), indent=4))
    except subprocess.CalledProcessError as e:
        error_msg = f"Command '{e.cmd}' returned non-zero exit status {e.returncode}."
        ret = createReturn(code=500, val=error_msg)
        return print(json.dumps(json.loads(ret), indent=4))
    except Exception as e:
        ret = createReturn(code=500, val=f"Error: {str(e)}")
        return print(json.dumps(json.loads(ret), indent=4))

def list_gfs():
    try:
        vgs_result = subprocess.run(
            ["vgs", "-o", ",vg_name", "--reportformat", "json"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )
        vgs_output = json.loads(vgs_result.stdout)
        gfs_vgs = []
        for vg in vgs_output["report"][0]["vg"]:
            vg_name = vg.get("vg_name", "")
            if "vg_glue" in vg_name:
                vg_name = vg.get("vg_name", "")
                gfs_vgs.append({
                    "vg_name": vg_name,
                })
        # 결과 반환
        ret = createReturn(code=200, val=gfs_vgs)
        return print(json.dumps(json.loads(ret), indent=4))
    except subprocess.CalledProcessError as e:
        error_msg = f"Command '{e.cmd}' returned non-zero exit status {e.returncode}."
        ret = createReturn(code=500, val=error_msg)
        return print(json.dumps(json.loads(ret), indent=4))
    except Exception as e:
        ret = createReturn(code=500, val=f"Error: {str(e)}")
        return print(json.dumps(json.loads(ret), indent=4))

def delete_clvm(vg_names,pv_names,disks):
    try:
        for vg_name, pv_name in zip(vg_names, pv_names):
            run_command(f"vgremove {vg_name}")
            run_command(f"pvremove {pv_name}")
            multipath_check = os.popen("multipath -l -v 1").read().strip()
            if multipath_check != "" :
                mpath_name = re.sub(r'\d+$', '', pv_name)

                run_command(f'echo -e "d\nw" | fdisk {mpath_name}')
                for disk_id in disks:
                    disk_id = disk_id.replace("dm-uuid-part1-mpath-","dm-uuid-mpath-")
                    for host in json_data["clusterConfig"]["hosts"]:
                        ssh_client = connect_to_host(host["ablecube"])
                        run_command(f"partprobe {disk_id}",ssh_client)
            else:
                disk_name = re.sub(r'\d+$', '', pv_name)
                run_command(f"parted -s {disk_name} rm 1")
                for host in json_data["clusterConfig"]["hosts"]:
                    ssh_client = connect_to_host(host["ablecube"])
                    single_disk_arr = run_command("lsblk -r -n -o NAME,TYPE -d | grep -v rom | awk '{print $1}'", ssh_client).split()
                    for single_disk in single_disk_arr:
                        run_command(f"partprobe /dev/{single_disk}",ssh_client)


        ret = createReturn(code=200, val="Success to clvm delete")
        return print(json.dumps(json.loads(ret), indent=4))
    except Exception as e:
        ret = createReturn(code=500, val=f"Error: {str(e)}")
        return print(json.dumps(json.loads(ret), indent=4))
def delete_gfs(disks, gfs_name, lv_name, vg_name):
    try:
        run_command(f"pcs resource disable {gfs_name}")
        run_command(f"pcs resource disable {gfs_name}_res")

        time.sleep(8)

        run_command(f"pcs resource delete {gfs_name} --force")

        time.sleep(8)

        run_command(f"pcs resource delete {gfs_name}_res --force")

        run_command("pcs resource cleanup")

        run_command(f"vgchange --lock-type none --lock-opt force {vg_name} -y ")
        run_command(f"vgchange -aey {vg_name}")
        run_command(f"lvremove --lockopt skiplv /dev/{vg_name}/{lv_name} -y")
        run_command(f"vgremove {vg_name}")
        for partition in disks:
            multipath_check = os.popen("multipath -l -v 1").read().strip()
            if multipath_check != "" :
                disk = partition.replace("dm-uuid-part1-mpath-","dm-uuid-mpath-")
                run_command(f"pvremove {partition}")
                run_command(f"echo -e 'd\nw\n' | fdisk {disk} >/dev/null 2>&1")

                for host in json_data["clusterConfig"]["hosts"]:
                    ssh_client = connect_to_host(host["ablecube"])
                    # escaped_disk = disk.replace('/', '\\/')
                    # escaped_partition = partition.replace('/', '\\/')
                    # sed_cmd = f"sed -i '/partprobe {escaped_disk}/{{N; /lvmdevices --adddev -y {escaped_partition}/d;}}' /etc/rc.local /etc/rc.d/rc.local"

                    # lvm.conf 초기화
                    run_command(f"partprobe {disk}",ssh_client,ignore_errors=True)
                    # run_command(sed_cmd, ssh_client, ignore_errors=True)

                    ssh_client.close()
            else:
                run_command(f"pvremove {partition}")
                run_command(f"parted -s {disk} rm 1")

                for host in json_data["clusterConfig"]["hosts"]:
                    ssh_client = connect_to_host(host["ablecube"])
                    single_disk_arr = run_command("lsblk -r -n -o NAME,TYPE -d | grep -v rom | awk '{print $1}'", ssh_client).split()
                    for single_disk in single_disk_arr:
                        # single_partition = f"/dev/{single_disk}1"
                        # escaped_disk = single_disk.replace('/', '\\/')
                        # escaped_partition = single_partition.replace('/', '\\/')
                        # sed_cmd = f"sed -i '/partprobe /dev/{escaped_disk}/{{N; /lvmdevices --adddev -y /dev/{escaped_partition}/d;}}' /etc/rc.local /etc/rc.d/rc.local"

                        # lvm.conf 초기화
                        run_command(f"partprobe /dev/{single_disk}",ssh_client,ignore_errors=True)
                        # run_command(sed_cmd, ssh_client, ignore_errors=True)

                    ssh_client.close()

        ret = createReturn(code=200, val="Success to gfs delete")
        return print(json.dumps(json.loads(ret), indent=4))
    except Exception as e:
        ret = createReturn(code=500, val=f"Error: {str(e)}")
        return print(json.dumps(json.loads(ret), indent=4))

def list_hba_wwn():
    try:
        hosts = json_data["clusterConfig"]["hosts"]

        def get_hba_wwn(host):
            """개별 호스트에서 HBA WWN 정보를 가져오는 내부 함수"""
            try:
                ip = host["ablecube"]
                hostname = host["hostname"]
                ssh_client = connect_to_host(ip)

                hba_check = run_command("lspci | grep -i fibre", ssh_client)
                wwn_list = []
                if hba_check:
                    wwn_list = run_command("cat /sys/class/fc_host/host*/port_name", ssh_client).split()

                ssh_client.close()
                return {"hostname": hostname, "wwn": wwn_list}

            except Exception as e:
                return {"hostname": host["hostname"], "wwn": [], "error": str(e)}

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            results = list(executor.map(get_hba_wwn, hosts))

        ret = createReturn(code=200, val=results)
        print(json.dumps(json.loads(ret), indent=4))

    except Exception as e:
        ret = createReturn(code=500, val=f"Error: {str(e)}")
        print(json.dumps(json.loads(ret), indent=4))

def rescan_and_extend_gfs_disk(action, vg_name, lv_name, mount_point, mpath_disks, gfs_name, non_stop_check):
    try:
        if action == "rescan":
            for i in range(len(json_data["clusterConfig"]["hosts"])):
                host = json_data["clusterConfig"]["hosts"][i]
                ip = host["ablecube"]
                ssh_client = connect_to_host(ip)
                disk_list = run_command(f"python3 {pluginpath}/python/disk/disk_action.py mpath-list", ssh_client)
                disk_list = json.loads(disk_list)
                gfs_disks = []
                mpath_name = []
                mpath_path = []

                for bd in disk_list['val']['blockdevices']:
                    if 'children' in bd and bd['children']:
                        first_level = bd['children'][0]
                        if 'children' in first_level and first_level['children']:
                            second_level = first_level['children'][0]
                            if 'children' in second_level and second_level['children']:
                                third_level = second_level['children'][0]
                                if third_level.get('name') == vg_name+"-"+lv_name:
                                    if bd['name'] not in gfs_disks:
                                        gfs_disks.append(bd['name'])

                                    if first_level['name'] not in mpath_name:
                                        mpath_name.append(first_level['name'])

                                    if first_level['path'] not in mpath_path:
                                        mpath_path.append(first_level['path'])
                for disk in gfs_disks:
                    run_command(f"echo 1 > /sys/block/{disk}/device/rescan", ssh_client)
                for mpath in mpath_name:
                    run_command(f'multipathd -k"resize map {mpath}"', ssh_client)
                ssh_client.close()

            ret = createReturn(code=200, val="Success to scan GFS Disk")
            print(json.dumps(json.loads(ret), indent=4))

        elif action == "extend":
            if non_stop_check == "true":
                run_command("pcs property set maintenance-mode=true")

            disk_list = run_command(f"python3 {pluginpath}/python/disk/disk_action.py mpath-list")
            disk_list = json.loads(disk_list)
            gfs_disks = []
            mpath_name = []
            mpath_path = []
            mpath_path_partition = []
            lv_path = []
            for bd in disk_list['val']['blockdevices']:
                if 'children' in bd and bd['children']:
                    first_level = bd['children'][0]
                    if 'children' in first_level and first_level['children']:
                        second_level = first_level['children'][0]
                        if 'children' in second_level and second_level['children']:
                            third_level = second_level['children'][0]
                            if third_level.get('name') == vg_name+"-"+lv_name:
                                if bd['name'] not in gfs_disks:
                                    gfs_disks.append(bd['name'])


                                if first_level['name'] not in mpath_name:
                                    mpath_name.append(first_level['name'])

                                if first_level['path'] not in mpath_path:
                                    mpath_path.append(first_level['path'])

                                partition_path = first_level['path'] + "1"
                                if partition_path not in mpath_path_partition:
                                    mpath_path_partition.append(partition_path)

                                if third_level['path'] not in lv_path:
                                    lv_path.append(third_level['path'])

            for path in mpath_path:
                run_command(f"parted -s {path} resizepart 1 100% -f")
                for i in range(len(json_data["clusterConfig"]["hosts"])):
                    host = json_data["clusterConfig"]["hosts"][i]
                    ip = host["ablecube"]
                    ssh_client = connect_to_host(ip)
                    run_command(f"partprobe {path}", ssh_client)
                ssh_client.close()

            for path_partition in mpath_path_partition:
                run_command(f"pvresize {path_partition}")

            run_command(f"lvextend -l +100%FREE {vg_name}/{lv_name}")
            run_command(f"gfs2_grow {mount_point}")

            for path in mpath_path:
                for i in range(len(json_data["clusterConfig"]["hosts"])):
                    host = json_data["clusterConfig"]["hosts"][i]
                    ip = host["ablecube"]
                    ssh_client = connect_to_host(ip)
                    run_command(f"partprobe {path}", ssh_client)
                ssh_client.close()

            if non_stop_check == "true":
                run_command("pcs property set maintenance-mode=false")

            ret = createReturn(code=200, val=f"Success to extend GFS Disk")
            print(json.dumps(json.loads(ret), indent=4))

        elif action == "scan":
            for i in range(len(json_data["clusterConfig"]["hosts"])):
                host = json_data["clusterConfig"]["hosts"][i]
                ip = host["ablecube"]
                ssh_client = connect_to_host(ip)
                run_command("for host in /sys/class/scsi_host/*; do echo '- - -' > '$host/scan'; done", ssh_client)
            ssh_client.close()

            ret = createReturn(code=200, val=f"Success to scan GFS Disk")
            print(json.dumps(json.loads(ret), indent=4))

        elif action == "add-extend":
            if non_stop_check == "true":
                run_command("pcs property set maintenance-mode=true")

            mpath_partition = []

            for mpath in mpath_disks:
                disk_partition = mpath.replace("dm-uuid-mpath-","dm-uuid-part1-mpath-")
                mpath_partition.append(disk_partition)
                run_command(f"parted -s {mpath} mklabel gpt mkpart {gfs_name} 0% 100% set 1 lvm on")
                run_command(f"pvcreate {disk_partition}")

            for mpath in mpath_disks:
                disk_partition = mpath.replace("dm-uuid-mpath-","dm-uuid-part1-mpath-")
                for i in range(len(json_data["clusterConfig"]["hosts"])):
                    host = json_data["clusterConfig"]["hosts"][i]
                    ip = host["ablecube"]
                    ssh_client = connect_to_host(ip)
                    run_command(f"partprobe {mpath}", ssh_client)
                    run_command(f"lvmdevices --adddev {disk_partition}", ssh_client)
                ssh_client.close()

            run_command(f"vgextend {vg_name} {' '.join(mpath_partition)}")
            run_command(f"lvextend -l +100%FREE /dev/{vg_name}/{lv_name}")

            run_command(f"gfs2_grow {mount_point}")

            if non_stop_check == "true":
                run_command("pcs property set maintenance-mode=false")

            ret = createReturn(code=200, val=f"Success to Extend Add GFS Disk")
            print(json.dumps(json.loads(ret), indent=4))

    except Exception as e:
        ret = createReturn(code=500, val=f"Failed to Rescan and Extend GFS Disk: {str(e)}")
        print(json.dumps(json.loads(ret), indent=4))
def main():
    parser = argparse.ArgumentParser(description="Cluster configuration script")

    parser.add_argument('--create-clvm', action='store_true', help='Flag to create CLVM Disk.')
    parser.add_argument('--list-clvm', action='store_true', help='Comma separated list of CLVM Disk.')
    parser.add_argument('--list-gfs', action='store_true', help='List GFS Volume Groups.')
    parser.add_argument('--delete-clvm', action='store_true', help='Delete CLVM Volume Group.')
    parser.add_argument('--delete-gfs', action='store_true', help='Delete GFS Disk.')
    parser.add_argument('--list-hba-wwn', action='store_true', help='List HBA WWN.')
    parser.add_argument('--disks', help='Comma separated list of disks to use.')
    parser.add_argument('--gfs-name', help='GFS Name')
    parser.add_argument('--lv-names', help='Serveral LV Name.')
    parser.add_argument('--vg-names', help='Serveral VG Name.')
    parser.add_argument('--pv-names', help='Serveral PV Name.')
    parser.add_argument('--mount-point', help='Mount point for GFS Disk.')
    parser.add_argument('--rescan', action='store_true',help='Rescan GFS Disk.')
    parser.add_argument('--extend', action='store_true',help='Extend GFS Disk.')
    parser.add_argument('--scan', action='store_true',help='Scan HBA GFS Disk.')
    parser.add_argument('--add-extend', action='store_true',help='Add Disk Extend GFS Disk.')
    parser.add_argument('--non-stop-check', help='Non-stop check for GFS Disk.')
    args = parser.parse_args()

    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(1)

    if args.create_clvm:
        if not all([args.disks]):
            print("Please provide both '--disks' when using '--create-clvm'.")
            parser.print_help()
        else:
            disks = args.disks.split(',')
            create_clvm(disks)

    if args.list_clvm:
        list_clvm()

    if args.list_gfs:
        list_gfs()

    if args.delete_clvm:
        if not all ([args.vg_names, args.pv_names, args.disks]):
            print("Please provide both '--vg-names' and '--pv-names' and '--disks' when using '--delete-clvm'.")
            parser.print_help()
        else:
            vg_names = args.vg_names.split(',')
            pv_names = args.pv_names.split(',')
            disk_ids = args.disks.split(',')
        delete_clvm(vg_names, pv_names, disk_ids)

    if args.delete_gfs:
        if not all ([args.disks, args.gfs_name, args.lv_names, args.vg_names]):
            print("Please provide both '--disks', '--gfs-name', '--vg-names' and '--lv-names' when using '--delete-gfs'.")
            parser.print_help()
        else:
            disks = args.disks.split(',')
            delete_gfs(disks, args.gfs_name, args.lv_names, args.vg_names)

    if args.list_hba_wwn:
        list_hba_wwn()

    if args.rescan:
        if not all ([args.vg_names, args.lv_names, args.mount_point]):
            print("Please provide both '--vg-names' and '--lv-names' and '--mount-point' when using '--rescan'.")
            parser.print_help()
        else:
            rescan_and_extend_gfs_disk("rescan", args.vg_names, args.lv_names, args.mount_point, None, None, None)
    elif args.extend:
        if not all ([args.vg_names, args.lv_names, args.mount_point]):
            print("Please provide both '--vg-names' and '--lv-names' and '--mount-point', '--non-stop-check' when using '--extend'.")
            parser.print_help()
        else:
            rescan_and_extend_gfs_disk("extend", args.vg_names, args.lv_names, args.mount_point, None, None, args.non_stop_check)
    elif args.scan:
        rescan_and_extend_gfs_disk("scan", None, None, None, None, None, None)
    elif args.add_extend:
        if not all ([args.vg_names, args.lv_names, args.mount_point, args.disks, args.gfs_name, args.non_stop_check]):
            print("Please provide both '--vg-names' and '--lv-names' and '--mount-point', '--disks', '--gfs-name', '--non-stop-check' when using '--add-extend'.")
            parser.print_help()
        else:
            mpath_disks = args.disks.split(',')
            rescan_and_extend_gfs_disk("add-extend", args.vg_names, args.lv_names, args.mount_point, mpath_disks, args.gfs_name, args.non_stop_check)

if __name__ == "__main__":
    main()
