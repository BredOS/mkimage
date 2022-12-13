#! /usr/bin/python

import argparse
import logging
import os
import pathlib
from signal import signal, SIGINT
import subprocess
import sys
import time
import prettytable

parser = argparse.ArgumentParser(
    description='Create archlinux arm based images.'
    )
parser.add_argument(
    '-w',
    '--work_dir',
    help='Directory to work in',
    required=True
    )
parser.add_argument(
    '-c',
    '--config_dir',
    help='Folder with config files',
    required=True
    )
parser.add_argument(
    '-o',
    '--out_dir',
    help='Folder to put output files',
    required=True
    )
args = parser.parse_args()
# Convert relative path to absolute path
def abspath(path):
    return os.path.abspath(path)
work_dir = abspath(args.work_dir) + "/"
config_dir = abspath(args.config_dir) + "/"
out_dir = abspath(args.out_dir) + "/"
mnt_dir = work_dir + "mnt/"
if os.geteuid() != 0:
        exit("Error: Run this script as root")
LOGGING_FORMAT: str = '%(asctime)s [%(levelname)s] %(message)s (%(funcName)s)'
LOGGING_DATE_FORMAT: str = '%H:%M:%S'

# 
def verify_config():
    if not os.path.exists(config_dir):
        logging.error("Config directory " + config_dir + " does not exist")
        exit(1)
    if not os.path.exists(out_dir):
        os.mkdir(out_dir)
    if not os.path.exists(work_dir):
        os.mkdir(work_dir)
    sys.path.insert(0, work_dir)
    subprocess.run(["cp", config_dir + "/profiledef",  work_dir + "/profiledef.py"])
    # Empty all variables
    img_name=""
    img_version=""
    install_dir=""
    arch=""
    device=""
    fs=""
    from profiledef import arch, cmdline, configtxt, device, edition, fs, img_backend, img_name, img_type, img_version # type: ignore
    packages_file=config_dir + "packages." + arch
    if not arch == 'aarch64' or arch == 'armv7h':
        logging.error("arch incompatable use aarch64 or armv7h")
        exit(1)
    if not img_name:
        logging.error("Image name not set")
        exit(1)
    if not img_version:
        logging.error("Image version not set")
        exit(1)
    if not install_dir:
        install_dir=work_dir + "/" + arch
        subprocess.run(["mkdir","-p", install_dir])
    if fs == 'ext4' or fs == 'btrfs':
        pass
    else:
        logging.error("Filesystem not supported use ext4 or btrfs")
        exit(1)
    if device == 'rpi' or device == 'odroid' or device == 'generic':
        pass
    else:
        logging.error("Device not supported use rpi, odroid or generic")
        exit(1)
    if not os.path.isfile(packages_file):
        logging.error("packages file doesnt exist create the file packages." + arch)
        exit(1)
    if img_type == 'image' or img_type == 'rootfs' or img_type == 'qcow2':
        pass
    else:
        logging.error("Image type not supported use image, rootfs or qcow2")
        exit(1)
    if img_backend == 'loop' or img_backend == 'qemu-nbd':
        pass
    else:
        logging.error("Image backend not supported use loop or qemu-nbd")
        exit(1)
    with open(packages_file, 'r') as f:
        packages = map(
            lambda package: package.strip(),
            f.readlines()
        )
        packages = list(filter(
            lambda package: not package.startswith('#'),
            packages
        ))
    return img_name, img_version, abspath(install_dir), arch, packages, packages_file , device, fs, img_type, img_backend, configtxt,cmdline,edition
    

def runonce(thing):
    runonce_path=pathlib.Path("/tmp/runonce_"+thing)
    if runonce_path.exists():
        return False
    else:
        return True

def get_partuuid(device):
    return subprocess.check_output(['blkid', device]).decode("utf-8").split(" ")[-1].split('"')[-2]


def pacstrap_packages(pacman_conf, packages_file, install_dir):
    with open(packages_file, 'r') as f:
        packages = map(
            lambda package: package.strip(),
            f.readlines()
        )
        packages = list(filter(
            lambda package: not package.startswith('#'),
            packages
        ))
    logging.info("Install dir is:" + install_dir)
    logging.info("Running pacstrap")
    subprocess.run(
        [
            "pacstrap",
            "-d", "-c",
            "-C", pacman_conf, 
            "-G", install_dir
        ] + packages
    )
    logging.info("Pacstrap complete")


def makeimg(size,fs,img_name,backend):
    format = "raw"
    image_ext = ".img"
    if fs == "btrfs":
        img_size = size - int(390000)
    else:
        img_size = size + int(1100000)
    
    if img_name == "qcow2":        
        logging.info("Creating image file " + img_name + ".qcow2")
        subprocess.run(
            [
                'qemu-img', 'create', '-f', 'qcow2', 
                work_dir + '/' + img_name + '.qcow2',
                str(img_size)+ "K"
            ]
        )
    else:
        logging.info("Creating image file " + img_name + ".img")
        subprocess.run(
            [
                'dd', 
                'if=/dev/zero', 
                'of=' + work_dir + '/' + img_name + '.img',
                'bs=1k',
                'count=' + str(img_size)
            ]
        )
    if backend == "qemu-nbd":
        subprocess.run(
            [
                "qemu-nbd",
                "--connect",
                ldev,
                work_dir + '/' + img_name + image_ext,
                 "--format", format
            ]
        )
    else:
        subprocess.run(['modprobe','loop'])
        logging.info("Attaching image file " + img_name + ".img to loop device " + ldev)
        subprocess.run(['losetup', ldev, work_dir + '/' + img_name + '.img'])
    return img_size,ldev

def partition_rpi(disk,fs,img_size):
    table=[
        ["Partition", "Start", "End","Size", "Filesystem"],
        ["boot", "0%", "150M", "150M", "fat32"],
        ["root", "150M", "100%", str(int(img_size/1000)-150) + "M" , fs]]
    table_pretty = prettytable.PrettyTable(table[0])
    for row in table[1:]:
        table_pretty.add_row(row)
    logging.info("\n"+table_pretty.get_string(title=disk+" Size " + str(int(img_size/1000)) + "M"))
    subprocess.run(
        [
            "parted",
            "--script", disk,
            "mklabel", "msdos",
            "mkpart", "primary", "fat32", "0%", "150M",
            "mkpart", "primary", fs, "150M", "100%"
        ]
    )
    subprocess.run(["mkfs.fat", "-F32", "-n", "BOOT", disk + "p1"])
    try:
        os.mkdir(work_dir + "/mnt")
    except FileExistsError:
        pass    
    p2 = disk+"p2 "
    if fs == "btrfs":
        subprocess.run("mkfs.btrfs -f -L ROOTFS " + p2,shell=True)
        subprocess.run("mount -t btrfs -o compress=zstd " + p2 + mnt_dir,shell=True)
        subprocess.run("btrfs subvolume create " + mnt_dir + "/@",shell=True)
        subprocess.run("btrfs subvolume create " + mnt_dir + "/@home",shell=True)
        subprocess.run("umount " + p2 ,shell=True)
        subprocess.run("mount -t btrfs -o compress=zstd,subvol=@ " + p2 + mnt_dir,shell=True)
        os.mkdir(mnt_dir + "/home")
        subprocess.run("mount -t btrfs -o compress=zstd,subvol=@home " + p2 + mnt_dir + "/home",shell=True)
    if fs == "ext4":
        subprocess.run("mkfs.ext4 -F -L ROOTFS " + p2,shell=True)
        subprocess.run("mount " + p2 + mnt_dir,shell=True)
    os.mkdir(mnt_dir + "/boot")

def partition_rock5b(disk,fs,img_size):
    table=[
        ["Partition", "Start", "End","Size", "Filesystem"],
        ["uboot", "0%", "16M", "16M", "NONE"],
        ["boot", "16M", "150M", "134M", "fat32"],
        ["root", "150M", "100%", str(int(img_size/1000)-150) + "M" , fs]]
    table_pretty = prettytable.PrettyTable(table[0])
    for row in table[1:]:
        table_pretty.add_row(row)
    logging.info("\n"+table_pretty.get_string(title=disk+" Size " + str(int(img_size/1000)) + "M"))
    subprocess.run(
        [
            "parted",
            "--script", disk,
            "mklabel", "gpt",
            "mkpart", "primary", "fat32", "16M", "150M",
            "set", "1", "boot", "on",
            "set", "1", "esp", "on",
            "mkpart", "primary", fs, "150M", "100%"
        ]
    )
    subprocess.run(["mkfs.fat", "-F32", "-n", "BOOT", disk + "p1"])
    subprocess.run("dd if=" + work_dir + "/idbloader.img of=" + disk + " bs=512 seek=64",shell=True)
    subprocess.run("dd if=" + work_dir + "/u-boot.itb of=" + disk + " bs=512 seek=16384",shell=True)
    if fs == "ext4":
        subprocess.run("mkfs.ext4 -F -L ROOTFS " + disk + "p2",shell=True)
        subprocess.run("mount " + disk + "p2 " + mnt_dir,shell=True)
    else:
        exit(1)

def partition_rockpro(disk,fs,img_size):
    table=[
        ["Partition", "Start", "End","Size", "Filesystem"],
        ["uboot", "0%", "32M", "32M", "None"],
        ["root", "32M", "100%", str(int(img_size/1000)-32) + "M" , fs]]
    table_pretty = prettytable.PrettyTable(table[0])
    for row in table[1:]:
        table_pretty.add_row(row)
    logging.info("\n"+table_pretty.get_string(title=disk+" Size " + str(int(img_size/1000)) + "M"))
    input("Press Enter to continue...")
    # logging.info(table_pretty.get_string(title=disk))
    subprocess.run(
        [
            "parted", "-s",
            disk, "mklabel", "msdos",
            disk, "mkpart", "primary", fs, "32M", "100%"
        ]
    )
    os.chdir(work_dir)
    subprocess.run(["wget", "http://os.archlinuxarm.org/os/rockchip/boot/rock64/rksd_loader.img", "http://os.archlinuxarm.org/os/rockchip/boot/rock64/rksd_loader.img"])
    # dd if=rksd_loader.img of=/dev/sdX seek=64 conv=notrunc
    subprocess.run(
        [
            "dd",
            "if=rksd_loader.img",
            "of=" + disk,
            "seek=64", "conv=notrunc"
        ]
    )
    #dd if=u-boot.itb of=/dev/sdX seek=16384 conv=notrunc
    subprocess.run(
        [
            "dd",
            "if=u-boot.itb",
            "of=" + disk,
            "seek=16384", "conv=notrunc"
        ]
    )
    try:
        os.mkdir(work_dir + "/mnt")
    except FileExistsError:
        pass
    p1 = disk+"p1 "
    if fs == "btrfs":
        subprocess.run("mkfs.btrfs -f -L ROOTFS " + p1,shell=True)
        subprocess.run("mount -t btrfs -o compress=zstd " + p1 + mnt_dir,shell=True)
        subprocess.run("btrfs subvolume create " + mnt_dir + "/@",shell=True)
        subprocess.run("btrfs subvolume create " + mnt_dir + "/@home",shell=True)
        subprocess.run("umount " + p1 ,shell=True)
        subprocess.run("mount -t btrfs -o compress=zstd,subvol=@ " + p1 + mnt_dir,shell=True)
        os.mkdir(mnt_dir + "/home")
        subprocess.run("mount -t btrfs -o compress=zstd,subvol=@home " + p1 + mnt_dir + "/home",shell=True)
    if fs == "ext4":
        subprocess.run("mkfs.ext4 -F -L ROOTFS " + p1,shell=True)
        subprocess.run("mount " + p1 + mnt_dir,shell=True)



def compressimage(img_name):
    logging.info("Compressing "+img_name+".img")
    subprocess.run(
        [
            "xz",
            "-k", "--extreme", "--best", "-T0","--verbose", "-f",
            work_dir + '/' + img_name + '.img'
        ]
    )
    # Move the image to the correct output directory
    subprocess.run(
        [
            "mv",
            work_dir + '/' + img_name + '.img.xz',
            out_dir + '/' + img_name + '.img.xz'
        ]
    )
    logging.info("Compressed "+img_name+".img")

def copyfiles(ot, to,retainperms=False):
    logging.info("Copying files to " + to)
    if retainperms:
        subprocess.run("cp -apr " + ot + "/* " +  to,shell=True)
    else:
        subprocess.run("cp -ar " + ot + "/* " +  to,shell=True)

def main():
    logging.basicConfig(
    format='%(asctime)s %(levelname)s: %(message)s',
    datefmt=LOGGING_DATE_FORMAT,
    encoding='utf-8', level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(pathlib.Path(config_dir+"/mkimage.log"),mode="w")
    ])   
    logging.info("Initializing mkimage")
    build_date = time.strftime("%Y-%m-%d")
    pacman_conf = config_dir + "/pacman.conf."+arch
    logging.info("             Architecture:   "+arch)
    logging.info("                  Edition:   "+edition)
    logging.info("                  Version:   "+img_version)
    logging.info("        Working directory:   "+work_dir)
    logging.info("   Installation directory:   "+install_dir)
    logging.info("               Build date:   "+build_date)
    logging.info("         Output directory:   "+out_dir)
    logging.info("                   Device:   "+device)
    logging.info("               Filesystem:   "+fs)
    logging.info("               Image type:   "+img_type)
    logging.info("          Image file name:   "+img_name)
    logging.info("            Packages File:   "+packages_file)
    if device == "rpi":
        copyfiles(config_dir+ "/alarmimg",install_dir)
        pacstrap_packages(pacman_conf, packages_file, install_dir)
        subprocess.run(' '.join(["rm", "-rf",
            install_dir + "/etc/machine-id",
            install_dir + "/var/lib/dbus/machine-id"]),shell=True)
        subprocess.run(["sh", config_dir + "/fixperms.sh", install_dir])
        logging.info("Partitioning rpi")
        rootfs_size=int(subprocess.check_output(["du", "-s", install_dir]).split()[0].decode("utf-8"))
        img_size,ldev = makeimg(rootfs_size,fs,img_name,img_backend) 
        partition_rpi(ldev, fs, img_size)
        logging.info("Partitioned rpi successfully")
        if not os.path.exists(mnt_dir):
            os.mkdir(mnt_dir)
        subprocess.run("mount " + ldev+"p1 " + mnt_dir + "/boot",shell=True)
        copyfiles(install_dir, mnt_dir,retainperms=True)
        with open(mnt_dir + "/boot/cmdline.txt", "w") as f:
            root_uuid=get_partuuid(ldev+"p2")
            f.write("root=PARTUUID="+root_uuid+" " + cmdline)
        with open(work_dir + "/mnt/boot/config.txt", "a") as f:
            f.write(configtxt)
        if fs == "btrfs":
            with open(mnt_dir + "/etc/fstab", "a") as f:
                f.write("PARTUUID="+get_partuuid(ldev+"p2")+" / btrfs subvol=/@,defaults,compress=zstd,discard=async,ssd 0 0\n")
                f.write("PARTUUID="+get_partuuid(ldev+"p2")+" /home btrfs subvol=/@home,defaults,discard=async,ssd 0 0\n")
                f.write("PARTUUID="+get_partuuid(ldev+"p1")+" /boot vfat defaults 0 0\n")
        else:
            with open(mnt_dir + "/etc/fstab", "a") as f:
                f.write("PARTUUID="+get_partuuid(ldev+"p2")+" / ext4 defaults 0 0\n")
                f.write("PARTUUID="+get_partuuid(ldev+"p1")+" /boot vfat defaults 0 0\n")
        
        subprocess.run(["umount", "-R", mnt_dir])
        if img_backend == "loop":
            subprocess.run(["losetup", "-d", ldev])
        elif img_backend == "qemu-nbd":
            subprocess.run(["qemu-nbd", "-d", ldev])
        compressimage(img_name)
        subprocess.run(["chmod", "-R", "777", out_dir])
        subprocess.run(["rm", "-rf", work_dir])
    elif device == "rock5b":
        copyfiles(config_dir+ "/alarmimg",install_dir)
        pacstrap_packages(pacman_conf, packages_file, install_dir)
        subprocess.run(' '.join(["rm", "-rf",
            install_dir + "/etc/machine-id",
            install_dir + "/var/lib/dbus/machine-id"]),shell=True)
        subprocess.run(["sh", config_dir + "/fixperms.sh", install_dir])
        logging.info("Partitioning rock5b")
        rootfs_size=int(subprocess.check_output(["du", "-s", install_dir]).split()[0].decode("utf-8"))
        img_size,ldev = makeimg(rootfs_size,fs,img_name,img_backend)
        partition_rock5b(ldev, fs, img_size)
        logging.info("Partitioned rock5b successfully")
        if not os.path.exists(mnt_dir):
            os.mkdir(mnt_dir)
        subprocess.run("mount " + ldev+"p1 " + mnt_dir + "/boot",shell=True)
        copyfiles(install_dir, mnt_dir,retainperms=True)
        with open(mnt_dir + "/boot/extlinux/extlinux.conf", "w") as f:
            if not os.path.exists(mnt_dir + "/boot/extlinux"):
                os.mkdir(mnt_dir + "/boot/extlinux")
            f.write(configtxt)
        subprocess.run(["umount", "-R", mnt_dir])
        if img_backend == "loop":
            subprocess.run(["losetup", "-d", ldev])
        elif img_backend == "qemu-nbd":
            subprocess.run(["qemu-nbd", "-d", ldev])
        compressimage(img_name)
        subprocess.run(["chmod", "-R", "777", out_dir])
        subprocess.run(["rm", "-rf", work_dir])
        

def handler(signal_received, frame):
    # Handle any cleanup here
    logging.error('SIGINT or CTRL-C detected. Exiting gracefully')
    try:
        subprocess.run(["umount", "-R", mnt_dir])
    except:
        pass
    try:
        subprocess.run("umount -R " + install_dir + "/*" ,shell=True)
    except:
        pass
    try:
        if img_backend == "loop":
            subprocess.run(["losetup", "-d", ldev])
        elif img_backend == "qemu-nbd":
            subprocess.run(["qemu-nbd", "-d", ldev])
    except:
        pass
    exit(0)

if __name__ == "__main__":
    img_name, img_version, install_dir, arch, packages, packages_file , device, fs, img_type, img_backend,configtxt,cmdline,edition = verify_config()
    if img_backend == "loop":
        ldev=subprocess.check_output(['losetup', '-f']).decode('utf-8').strip("\n")
    elif img_backend == "qemu-nbd":
        subprocess.run(["modprobe","nbd"])
        ldev = "/dev/nbd2"
    signal(SIGINT, handler)
    main()

        