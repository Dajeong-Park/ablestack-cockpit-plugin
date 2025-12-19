import argparse
import os
import subprocess
from ablestack import *

def createArgumentParser():
    '''
    입력된 argument를 파싱하여 dictionary 처럼 사용하게 만들어 주는 parser를 생성하는 함수
    :return: argparse.ArgumentParser
    '''
    # 참조: https://docs.python.org/ko/3/library/argparse.html
    # 프로그램 설명
    parser = argparse.ArgumentParser(description='ccvm을 생성 하는 프로그램',
                                        epilog='copyrightⓒ 2021 All rights reserved by ABLECLOUD™',
                                        usage='%(prog)s arguments')

    # 인자 추가: https://docs.python.org/ko/3/library/argparse.html#the-add-argument-method
    parser.add_argument('action', choices=['create', 'copy', 'start', 'stop', 'delete'], help='choose one of the actions')
    parser.add_argument('--destroy', type=str, default='false',help='Force destroy the CCVM (true/false)')
    parser.add_argument('--purge', type=str, default='false',help='Force purge the CCVM images(true/false)')
    # output 민감도 추가(v갯수에 따라 output및 log가 많아짐):
    parser.add_argument('-v', '--verbose', action='count', default=0, help='increase output verbosity')

    # flag 추가(샘플임, 테스트용으로 json이 아닌 plain text로 출력하는 플래그 역할)
    parser.add_argument('-H', '--Human', action='store_const', dest='flag_readerble', const=True, help='Human readable')

    # Version 추가
    parser.add_argument('-V', '--Version', action='version', version='%(prog)s 1.0')

    return parser

def copy_file():
    """
    /root/.ssh/id_rsa, /root/.ssh/id_rsa.pub, /etc/hosts 를
    {pluginpath}/tools/vmconfig/ccvm/ 아래로 복사
    subprocess.run 사용
    """
    try:
        # 대상 디렉터리 생성
        target_dir = os.path.join(pluginpath, "tools", "vmconfig", "ccvm")
        os.makedirs(target_dir, exist_ok=True)

        # 파일 복사
        subprocess.run(["cp", "-f", "/etc/hosts", f"{target_dir}/hosts"], check=True)
        subprocess.run(["cp", "-f", "/root/.ssh/id_rsa", f"{target_dir}/id_rsa"], check=True)
        subprocess.run(["cp", "-f", "/root/.ssh/id_rsa.pub", f"{target_dir}/id_rsa.pub"], check=True)

        # 권한 설정
        subprocess.run(["chmod", "600", f"{target_dir}/id_rsa"], check=True)

        return createReturn(code=200, val="Files copied successfully.")
    except subprocess.CalledProcessError as e:
        return createReturn(code=500, val=f"Command failed: {e}")
    except Exception as e:
        return createReturn(code=500, val=f"Unexpected error: {e}")

def define_and_start_ccvm():
    old_xml_path = f"{pluginpath}/tools/vmconfig/ccvm/ccvm.xml"
    new_xml_path = "/mnt/glue/ccvm.xml"
    qcow_file = "/var/lib/libvirt/images/ablestack-template-back.qcow2"
    ccvm_file = "/mnt/glue/ccvm.qcow2"
    cmds = [
        f"cp {qcow_file} {ccvm_file}",
        f"cp {old_xml_path} {new_xml_path}",
        f"qemu-img resize {ccvm_file} +350G",
        f"virsh define --file {new_xml_path}",
        "virsh autostart ccvm",
        "virsh start ccvm",
        "virsh dominfo ccvm"
    ]

    try:
        for cmd in cmds:
            subprocess.run(cmd, shell=True, check=True,
                           capture_output=True, text=True)
        # 모든 단계가 성공했을 때
        return createReturn(code=200, val="CloudCenter VM created successfully.")
    except subprocess.CalledProcessError as e:
        # 어느 한 단계라도 실패 시
        return createReturn(code=500, val="Failed to create CloudCenter VM.")

def local_ccvm_start():
    try:
        subprocess.run("virsh start ccvm", shell=True, check=True,
                       capture_output=True, text=True)
        return createReturn(code=200, val="CloudCenter VM started successfully.")
    except subprocess.CalledProcessError as e:
        return createReturn(code=500, val="Failed to start CloudCenter VM.")
def local_ccvm_stop(destroy):
    try:
        if destroy.lower() == 'true':
            subprocess.run("virsh destroy ccvm", shell=True, check=True,
                           capture_output=True, text=True)
        else:
            subprocess.run("virsh shutdown ccvm", shell=True, check=True,
                        capture_output=True, text=True)

        return createReturn(code=200, val="CloudCenter VM stopped successfully.")
    except subprocess.CalledProcessError as e:
        return createReturn(code=500, val="Failed to stop CloudCenter VM.")
def local_ccvm_delete(purge):
    try:
        if purge.lower() == 'true':
            subprocess.run("virsh undefine ccvm --nvram", shell=True, check=True,
                        capture_output=True, text=True)
            subprocess.run("rm -rf /var/lib/libvirt/images/ccvm.qcow2", shell=True, check=True,
                        capture_output=True, text=True)
        else:
            subprocess.run("virsh undefine ccvm --nvram", shell=True, check=True,
                        capture_output=True, text=True)

        return createReturn(code=200, val="CloudCenter VM deleted successfully.")
    except subprocess.CalledProcessError as e:
        return createReturn(code=500, val="Failed to delete CloudCenter VM.")

if __name__ == "__main__":
    parser = createArgumentParser()
    # input 파싱
    args = parser.parse_args()

    if args.action == "create":
        ret = define_and_start_ccvm()
    elif args.action == "copy":
        ret= copy_file()
    elif args.action == "start":
        ret = local_ccvm_start()
    elif args.action == "stop":
        ret = local_ccvm_stop(args.destroy)
    elif args.action == "delete":
        ret = local_ccvm_delete(args.purge)

    print(ret)
