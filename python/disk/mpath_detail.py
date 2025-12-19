import json
import subprocess
import os
from ablestack import *

# 1. multipath -l 명령어로 dm-* 장치 리스트 추출
def get_dm_devices():
    result = subprocess.run(
        "multipath -l | grep mpath | awk '{print $3}'",
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    return set(result.stdout.strip().splitlines())

# 단일 디스크 장치 이름 리스트 추출 (예: sda, sdb, nvme0n1 등)
def get_single_devices():
    """
    lsblk --json 결과에서 물리 디스크(단일 경로) 이름만 추출합니다.
    - dm-* 장치는 제외합니다.
    - WWN 이 없는 장치는 스토리지 디스크가 아닐 수 있으므로 제외합니다.
    """
    result = subprocess.run(
        ["lsblk", "-o", "NAME,WWN", "--json"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True
    )

    data = json.loads(result.stdout)
    devices = set()

    for dev in data.get("blockdevices", []):
        name = dev.get("name")
        wwn = dev.get("wwn")

        if not name:
            continue

        # DM 장치는 싱글 디스크 대상이 아니므로 제외합니다.
        if name.startswith("dm-"):
            continue

        # WWN 이 없는 장치는 제외합니다.
        if not wwn:
            continue

        devices.add(name)

    return devices

# /dev/disk/by-id 내부 링크 분류 및 매핑
def map_links_by_dm(dm_devices):
    by_id_path = "/dev/disk/by-id"
    dm_map = {}

    for entry in os.listdir(by_id_path):
        full_path = os.path.join(by_id_path, entry)
        if os.path.islink(full_path):
            target = os.readlink(full_path)
            target_basename = os.path.basename(target)  # ../../dm-2 → dm-2

            if target_basename in dm_devices:
                # 분류
                if target_basename not in dm_map:
                    dm_map[target_basename] = {
                        "type" : "multipath",
                        "multipath_id": [],
                        "multipath_name": [],
                        "scsi": [],
                        "wwn": []
                    }

                if entry.startswith("dm-uuid-mpath"):
                    dm_map[target_basename]["multipath_id"].append("/dev/disk/by-id/" + entry)
                elif entry.startswith("dm-name-mpath"):
                    mpath_name = entry.replace("dm-name-", "")
                    dm_map[target_basename]["multipath_name"].append("/dev/mapper/" + mpath_name)
                elif entry.startswith("scsi-"):
                    dm_map[target_basename]["scsi"].append(entry)
                elif entry.startswith("wwn-"):
                    dm_map[target_basename]["wwn"].append(entry)

    # dm-* 이름을 숫자 기준으로 정렬
    sorted_dm_map = dict(
        sorted(dm_map.items(), key=lambda x: int(x[0].split('-')[1]))
    )
    return sorted_dm_map

# /dev/disk/by-id 내부 링크 분류 및 매핑 (싱글 디스크용)
def map_links_by_single(single_devices):
    """
    /dev/disk/by-id 아래의 심볼릭 링크들을 순회하면서
    대상이 되는 단일 디스크 이름(single_devices)에 매핑합니다.
    """
    by_id_path = "/dev/disk/by-id"
    single_map = {}

    for entry in os.listdir(by_id_path):
        full_path = os.path.join(by_id_path, entry)

        if not os.path.islink(full_path):
            continue

        # ../../sda → sda
        target = os.readlink(full_path)
        target_basename = os.path.basename(target)

        if target_basename not in single_devices:
            continue

        # 장치별 초기 구조 생성
        if target_basename not in single_map:
            single_map[target_basename] = {
                "type": "single",
                "single_id": [],
                "single_name": [],
                "scsi": [],
                "wwn": []
            }

        # /dev/<name> 형태의 실제 디바이스 경로를 single_name 에 1번만 넣습니다.
        dev_path = os.path.join("/dev", target_basename)
        if not single_map[target_basename]["single_name"]:
            single_map[target_basename]["single_name"].append(dev_path)

        # /dev/disk/by-id/<entry> 풀 경로
        by_id_full_path = os.path.join(by_id_path, entry)

        # 종류별로 분류
        if entry.startswith("scsi-"):
            single_map[target_basename]["scsi"].append(by_id_full_path)
        elif entry.startswith("wwn-"):
            single_map[target_basename]["wwn"].append(by_id_full_path)
        else:
            # scsi-/wwn- 이 아닌 나머지 by-id 링크는 일반 ID 로 분류합니다.
            single_map[target_basename]["single_id"].append(by_id_full_path)

    # 장치 이름 기준 정렬
    sorted_single_map = dict(
        sorted(single_map.items(), key=lambda x: x[0])
    )
    return sorted_single_map

# 메인 로직
def result():
    try:
        mpath_status = os.popen("systemctl is-active multipathd").read().strip()

        if mpath_status == "active":
            dm_devices = get_dm_devices()
            mapped = map_links_by_dm(dm_devices)
        else:
            single_devices = get_single_devices()
            mapped = map_links_by_single(single_devices)
        return createReturn(code=200, val=mapped)
    except Exception as e:
        return createReturn(code=500, val=str(e))

if __name__ == "__main__":
    print(result())
