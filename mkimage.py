#! /usr/bin/python

import argparse
import logging
import os
import pathlib
from signal import SIGTERM, signal, SIGINT
import subprocess
import sys
import time
import datetime
import prettytable

parser = argparse.ArgumentParser(description="Create archlinux arm based images.")
parser.add_argument("-w", "--work_dir", help="Directory to work in", required=True)
parser.add_argument(
    "-x", "--no-compress", help="Do not compress into a .xz", action="store_true"
)
parser.add_argument(
    "-c", "--config_dir", help="Folder with config files", required=True
)
parser.add_argument("-o", "--out_dir", help="Folder to put output files", required=True)
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
LOGGING_FORMAT: str = "%(asctime)s [%(levelname)s] %(message)s (%(funcName)s)"
LOGGING_DATE_FORMAT: str = "%H:%M:%S"


def verify_config():
    cfg = dict()
    if not os.path.exists(config_dir):
        logging.error("Config directory " + config_dir + " does not exist")
        exit(1)
    if not os.path.exists(out_dir):
        os.mkdir(out_dir)
    cfg["out_dir"] = out_dir
    if not os.path.exists(work_dir):
        os.mkdir(work_dir)
    sys.path.insert(0, work_dir)
    subprocess.run(["cp", config_dir + "/profiledef", work_dir + "/profiledef.py"])

    import profiledef

    cfg["arch"] = profiledef.arch
    cfg["cmdline"] = profiledef.cmdline
    cfg["configtxt"] = profiledef.configtxt
    cfg["device"] = profiledef.device
    cfg["edition"] = profiledef.edition
    cfg["fs"] = profiledef.fs
    cfg["img_backend"] = profiledef.img_backend
    cfg["img_name"] = profiledef.img_name
    cfg["img_type"] = profiledef.img_type
    cfg["img_version"] = profiledef.img_version
    cfg["perms"] = profiledef.perms
    try:
        cfg["partition_table"] = profiledef.partition_table
    except AttributeError:
        cfg["partition_table_boot"] = profiledef.partition_table_boot
        cfg["partition_table_root"] = profiledef.partition_table_root
    cfg["partition_extras"] = profiledef.partition_extras
    cfg["config_dir"] = config_dir
    cfg["work_dir"] = work_dir

    packages_file = config_dir + "packages." + cfg["arch"]
    cfg["packages_file"] = packages_file

    if cfg["arch"] not in ["aarch64", "armv7h"]:
        logging.error("Arch incompatible. Use aarch64 or armv7h")
        exit(1)
    if not cfg["img_name"]:
        logging.error("Image name not set")
        exit(1)
    if not cfg["img_version"]:
        logging.error("Image version not set")
        exit(1)

    install_dir = work_dir + "/" + cfg["arch"]
    cfg["install_dir"] = install_dir
    subprocess.run(["mkdir", "-p", install_dir])

    if cfg["fs"] not in ["ext4", "btrfs"]:
        logging.error("Filesystem not supported use ext4 or btrfs")
        exit(1)
    # == 'rpi' or device == 'rock5b' or device == 'generic' or device == 'vim4' or device == 'cpi4' or device
    if cfg["device"] in [
        "rpi",
        "rock5b",
        "rock5b-split",
        "cpi4",
        "generic",
        "vim4",
        "edge2",
        "rock4c-plus",
    ]:
        pass
    else:
        logging.error(
            "Device not supported use rpi, rock5b, rock5b-split, cpi4, edge2 or generic"
        )
        exit(1)
    if not os.path.isfile(packages_file):
        logging.error("packages file doesnt exist create the file packages." + arch)
        exit(1)
    if cfg["img_type"] not in ["image", "rootfs", "qcow2"]:
        logging.error("Image type not supported use image, rootfs or qcow2")
        exit(1)
    if cfg["img_backend"] not in ["loop", "qemu-nbd"]:
        logging.error("Image backend not supported use loop or qemu-nbd")
        exit(1)

    with open(packages_file, "r") as f:
        packages = map(lambda package: package.strip(), f.readlines())
        packages = list(filter(lambda package: not package.startswith("#"), packages))
    return cfg


def runonce(thing) -> bool:
    runonce_path = pathlib.Path("/tmp/runonce_" + thing)
    if runonce_path.exists():
        return False
    else:
        return True


def get_partuuid(device):
    return (
        subprocess.check_output(["blkid", device])
        .decode("utf-8")
        .split(" ")[-1]
        .split('"')[-2]
    )


def get_uuid(device):
    return (
        subprocess.check_output(["blkid", device])
        .decode("utf-8")
        .split(" ")[2]
        .split('"')[-2]
    )


def get_fat_uuid(device):  # I could do a smort one, but cmon, it's fine.
    return (
        subprocess.check_output(["blkid", device])
        .decode("utf-8")
        .split(" ")[3]
        .split('"')[-2]
    )


def realpath(item):
    return subprocess.check_output(["readlink", "-f", item]).decode("utf-8").split()[0]


def fixperms(target):
    realtarget = realpath(target)
    for i in cfg["perms"].keys():
        if realpath(realtarget + i) != realtarget + (i if not i[-1] == "/" else i[:-1]):
            raise OSError("Out of bounds permission fix!")
        if i[-1] == "/":
            subprocess.run(
                [
                    "chown",
                    "-Rh",
                    "--",
                    cfg["perms"][i][0] + ":" + cfg["perms"][i][1],
                    realtarget + i,
                ]
            )
        else:
            subprocess.run(
                [
                    "chown",
                    "-hv",
                    "--",
                    cfg["perms"][i][0] + ":" + cfg["perms"][i][1],
                    realtarget + i,
                ]
            )
        subprocess.run(["chmod", "--", cfg["perms"][i][2], realtarget + i])


def pacstrap_packages(pacman_conf, packages_file, install_dir) -> None:
    with open(packages_file) as f:
        packages = map(lambda package: package.strip(), f.readlines())
        packages = list(
            filter(
                lambda package: not (package.startswith("#") or not len(package)),
                packages,
            )
        )
    logging.info("Install dir is:" + install_dir)
    logging.info("Running pacstrap")
    subprocess.run(
        ["pacstrap", "-c", "-C", pacman_conf, "-M", "-G", install_dir] + packages,
        check=True,
    )
    logging.info("Pacstrap complete")


def makeimg(size, fs, img_name, backend):
    format = "raw"
    image_ext = ".img"
    if fs == "ext4":
        img_size = size - int(390000)
    elif fs == "btrfs":
        img_size = size + int(1100000)
    else:
        img_size = int(size)

    if img_name == "qcow2":
        logging.info("Creating image file " + img_name + ".qcow2")
        subprocess.run(
            [
                "qemu-img",
                "create",
                "-f",
                "qcow2",
                work_dir + "/" + img_name + ".qcow2",
                str(img_size) + "K",
            ]
        )
    else:
        logging.info("Creating image file " + img_name + ".img")
        subprocess.run(
            [
                "dd",
                "if=/dev/zero",
                "of=" + work_dir + "/" + img_name + ".img",
                "bs=1k",
                "count=" + str(img_size),
            ]
        )

    if backend == "qemu-nbd":
        subprocess.run(
            [
                "qemu-nbd",
                "--connect",
                ldev,
                work_dir + "/" + img_name + image_ext,
                "--format",
                format,
            ]
        )
    else:
        subprocess.run(["modprobe", "loop"])
        logging.info(
            "Attaching image file " + img_name + ".img to loop device " + next_loop()
        )
        ldev = next_loop()
        subprocess.run(["losetup", ldev, work_dir + "/" + img_name + ".img"])

    return img_size, ldev


def partition(disk, fs, img_size, partition_table, split=False):
    table = [["Partition", "Start", "End", "Size", "Filesystem"]]
    prtd_cmd = [
        "parted",
        "--script",
        disk,
        "--align",
        "optimal",
        "mklabel",
        "gpt",
    ]
    ld_partition_table = partition_table

    for i in ld_partition_table.keys():
        table.append([i] + partition_table[i])
        if partition_table[i][3] == "fat32":
            prtd_cmd += [
                "mkpart",
                "primary",
                "fat32",
                ld_partition_table[i][0],
                ld_partition_table[i][1],
                "set",
                "1",
                "boot",
                "on",
                "set",
                "1",
                "esp",
                "on",
            ]
        elif ld_partition_table[i][3] == "NONE":
            pass
        else:
            prtd_cmd += [
                "mkpart",
                "primary",
                fs,
                ld_partition_table[i][0],
                ld_partition_table[i][1],
            ]

    table_pretty = prettytable.PrettyTable(table[0])
    for row in table[1:]:
        table_pretty.add_row(row)
    logging.info(
        "\n"
        + table_pretty.get_string(
            title=disk + " Size " + str(int(img_size / 1000)) + "M"
        )
    )

    logging.info(f"Full command: {prtd_cmd}")
    subprocess.run(prtd_cmd)

    if not split:
        for i in cfg["partition_extras"](config_dir, disk):
            subprocess.run(i)

    if not os.path.exists(mnt_dir):
        os.mkdir(mnt_dir)

    idf = "p2" if not split else "p1"

    if fs == "ext4":
        subprocess.run("mkfs.ext4 -F -L PRIMARY " + disk + idf, shell=True)
        subprocess.run("mount " + disk + idf + " " + mnt_dir, shell=True)
        os.mkdir(mnt_dir + "/boot")
    elif fs == "btrfs":
        p2 = disk + idf + " "
        subprocess.run("mkfs.btrfs -f -L ROOTFS " + p2, shell=True)
        subprocess.run("mount -t btrfs -o compress=zstd " + p2 + mnt_dir, shell=True)
        for i in ["/@", "/@home", "/@log", "/@pkg", "/@.snapshots"]:
            subprocess.run("btrfs su cr " + mnt_dir + i, shell=True)
        subprocess.run("umount " + p2, shell=True)
        subprocess.run(
            "mount -t btrfs -o compress=zstd,subvol=@ " + p2 + mnt_dir, shell=True
        )
        os.mkdir(mnt_dir + "/home")
        subprocess.run(
            "mount -t btrfs -o compress=zstd,subvol=@home " + p2 + mnt_dir + "/home",
            shell=True,
        )
        os.mkdir(mnt_dir + "/boot")

    logging.info("Partitioned successfully")


def create_fstab(fs, ldev, ldev_alt=None, simple_vfat=False, no_discard=False) -> None:
    id1 = None
    id2 = None
    if ldev_alt is not None:
        id1 = get_fat_uuid(ldev + "p1")
        id2 = get_uuid(ldev_alt + "p1")
    else:
        id1 = get_fat_uuid(ldev + "p1")
        id2 = get_uuid(ldev + "p2")
    if fs == "ext4":
        with open(mnt_dir + "/etc/fstab", "a") as f:
            f.write("UUID=" + id1 + " / ext4 defaults 0 0\n")
            f.write(
                "UUID="
                + id2
                + " /boot"
                + "vfat rw,relatime,fmask=0022,dmask=0022,codepage=437,"
                + ("iocharset=ascii," if not simple_vfat else "")
                + "shortname=mixed,utf8,errors=remount-ro 0 2\n"
            )
    else:
        with open(mnt_dir + "/etc/fstab", "a") as f:
            f.write(
                "UUID="
                + id1
                + 28 * " "
                + "/boot"
                + 17 * " "
                + "vfat  rw,relatime,fmask=0022,dmask=0022,codepage=437,"
                + ("iocharset=ascii," if not simple_vfat else "")
                + "shortname=mixed,utf8,errors=remount-ro 0 2\n"
            )
            f.write(
                "UUID="
                + id2
                + " /"
                + 21 * " "
                + "btrfs rw,relatime,ssd"
                + (",discard=async" if not no_discard else "")
                + ",space_cache=v2,subvol=/@"
                + 35 * " "
                + "0 0\n"
            )
            f.write(
                "UUID="
                + id2
                + " /.snapshots"
                + 11 * " "
                + "btrfs rw,relatime,ssd,discard=async,space_cache=v2,subvol=/@.snapshots"
                + 25 * " "
                + "0 0\n"
            )
            f.write(
                "UUID="
                + id2
                + " /home"
                + 17 * " "
                + "btrfs rw,relatime,ssd,discard=async,space_cache=v2,subvol=/@home"
                + 31 * " "
                + "0 0\n"
            )
            f.write(
                "UUID="
                + id2
                + " /var/cache/pacman/pkg btrfs rw,relatime,ssd,discard=async,space_cache=v2,subvol=/@pkg"
                + 32 * " "
                + "0 0\n"
            )
            f.write(
                "UUID="
                + id2
                + " /var/log"
                + 14 * " "
                + "btrfs rw,relatime,ssd,discard=async,space_cache=v2,subvol=/@log"
                + 32 * " "
                + "0 0\n"
            )


def create_extlinux_conf(mnt_dir, configtxt, cmdline, ldev) -> None:
    if not os.path.exists(mnt_dir + "/boot/extlinux"):
        os.mkdir(mnt_dir + "/boot/extlinux")
        subprocess.run(["touch", mnt_dir + "/boot/extlinux/extlinux.conf"])
    with open(mnt_dir + "/boot/extlinux/extlinux.conf", "w") as f:
        f.write(configtxt)
        # add append root=UUID=... + cmdline
        if "partition_table_root" in cfg:
            root_uuid = get_uuid(ldev + "p1")
        else:
            root_uuid = get_uuid(ldev + "p2")
        f.write("    append root=UUID=" + root_uuid + " " + cmdline)


def cleanup(work_dir) -> None:
    logging.info("Cleaning up")
    subprocess.run(["rm", "-rf", work_dir])


def unmount(img_backend, mnt_dir, ldev, ldev_alt=None) -> None:
    logging.info("Unmounting!")
    subprocess.run(["umount", "-R", mnt_dir])
    if img_backend == "loop":
        subprocess.run(["losetup", "-d", ldev])
        if ldev_alt is not None:
            subprocess.run(["losetup", "-d", ldev_alt])
    elif img_backend == "qemu-nbd":
        subprocess.run(["qemu-nbd", "-d", ldev])


def compressimage(img_name) -> None:
    logging.info("Compressing " + img_name + ".img")
    subprocess.run(
        [
            "xz",
            "-k",
            "-9",
            "-T0",
            "--verbose",
            "-f",
            "-M",
            "65%",
            work_dir + "/" + img_name + ".img",
        ]
    )
    # Move the image to the correct output directory
    subprocess.run(
        [
            "mv",
            work_dir + "/" + img_name + ".img.xz",
            out_dir + "/" + img_name + ".img.xz",
        ]
    )
    subprocess.run(["chmod", "-R", "777", out_dir])
    logging.info("Compressed " + img_name + ".img")


def copyimage(img_name) -> None:
    logging.info("Copying " + img_name + ".img")
    # Move the image to the correct output directory
    subprocess.run(
        ["cp", work_dir + "/" + img_name + ".img", out_dir + "/" + img_name + ".img"]
    )
    subprocess.run(["chmod", "-R", "777", out_dir])
    logging.info("Copied " + img_name + ".img")


def copyfiles(ot, to, retainperms=False) -> None:
    logging.info("Copying files to " + to)
    if retainperms:
        subprocess.run("cp -apr " + ot + "/* " + to, shell=True)
    else:
        subprocess.run("cp -ar " + ot + "/* " + to, shell=True)


def machine_id():
    subprocess.run(
        " ".join(
            [
                "rm",
                "-r",
                cfg["install_dir"] + "/etc/machine-id",
                cfg["install_dir"] + "/var/lib/dbus/machine-id",
            ]
        ),
        shell=True,
    )


def main():
    logging.basicConfig(
        format="%(asctime)s %(levelname)s: %(message)s",
        datefmt=LOGGING_DATE_FORMAT,
        encoding="utf-8",
        level=logging.INFO,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(pathlib.Path(config_dir + "/mkimage.log"), mode="w"),
        ],
    )
    build_date = time.strftime("%Y-%m-%d")
    if cfg["install_dir"] is None:
        # In case for some weird reason it was none, it could break the builder machine.
        logging.info("install_dir is None!! ABORT!")
        exit(1)
    pacman_conf = cfg["config_dir"] + "/pacman.conf." + cfg["arch"]
    logging.info("             Architecture:   " + cfg["arch"])
    logging.info("                  Edition:   " + cfg["edition"])
    logging.info("                  Version:   " + cfg["img_version"])
    logging.info("        Working directory:   " + cfg["work_dir"])
    logging.info("   Installation directory:   " + cfg["install_dir"])
    logging.info("               Build date:   " + build_date)
    logging.info("         Output directory:   " + cfg["out_dir"])
    logging.info("                   Device:   " + cfg["device"])
    logging.info("               Filesystem:   " + cfg["fs"])
    logging.info("               Image type:   " + cfg["img_type"])
    logging.info("          Image file name:   " + cfg["img_name"])
    logging.info("            Packages File:   " + cfg["packages_file"])
    if cfg["device"] == "rpi":
        copyfiles(config_dir + "/alarmimg", cfg["install_dir"])
        fixperms(cfg["install_dir"])
        pacstrap_packages(pacman_conf, cfg["packages_file"], cfg["install_dir"])
        machine_id()
        fixperms(cfg["install_dir"])
        logging.info("Partitioning rpi")
        rootfs_size = int(
            subprocess.check_output(["du", "-s", cfg["install_dir"]])
            .split()[0]
            .decode("utf-8")
        )
        img_size, ldev = makeimg(
            rootfs_size, cfg["fs"], cfg["img_name"], cfg["img_backend"]
        )
        partition(
            ldev, cfg["fs"], img_size, cfg["partition_table"](img_size, cfg["fs"])
        )
        if not os.path.exists(mnt_dir):
            os.mkdir(mnt_dir)
        subprocess.run("mount " + ldev + "p1 " + mnt_dir + "/boot", shell=True)
        copyfiles(cfg["install_dir"], mnt_dir, retainperms=True)
        with open(mnt_dir + "/boot/cmdline.txt", "w") as f:
            root_uuid = get_uuid(ldev + "p2")
            f.write("root=UUID=" + root_uuid + " " + cmdline)
        with open(work_dir + "/mnt/boot/config.txt", "a") as f:
            f.write(configtxt)
        create_fstab(cfg["fs"], ldev)
        unmount(cfg["img_backend"], mnt_dir, ldev)
        cleanup(cfg["img_backend"])
        if args.no_compress:
            copyimage(cfg["img_name"])
        else:
            compressimage(cfg["img_name"])
        cleanup(cfg["work_dir"])
    elif cfg["device"] == "rock5b":
        copyfiles(config_dir + "/alarmimg", cfg["install_dir"])
        fixperms(cfg["install_dir"])
        pacstrap_packages(pacman_conf, cfg["packages_file"], cfg["install_dir"])
        machine_id()
        fixperms(cfg["install_dir"])
        logging.info("Partitioning rock5b")
        rootfs_size = int(
            subprocess.check_output(["du", "-s", cfg["install_dir"]])
            .split()[0]
            .decode("utf-8")
        )
        img_size, ldev = makeimg(
            rootfs_size, cfg["fs"], cfg["img_name"], cfg["img_backend"]
        )
        partition(
            ldev, cfg["fs"], img_size, cfg["partition_table"](img_size, cfg["fs"])
        )
        if not os.path.exists(mnt_dir):
            os.mkdir(mnt_dir)
        subprocess.run("mount " + ldev + "p1 " + mnt_dir + "/boot", shell=True)
        copyfiles(cfg["install_dir"], mnt_dir, retainperms=True)
        create_extlinux_conf(mnt_dir, cfg["configtxt"], cfg["cmdline"], ldev)
        create_fstab(cfg["fs"], ldev)
        unmount(cfg["img_backend"], mnt_dir, ldev)
        cleanup(cfg["img_backend"])
        if args.no_compress:
            copyimage(cfg["img_name"])
        else:
            compressimage(cfg["img_name"])
        cleanup(cfg["work_dir"])
    elif cfg["device"] == "rock5b-split":
        copyfiles(config_dir + "/alarmimg", cfg["install_dir"])
        fixperms(cfg["install_dir"])
        pacstrap_packages(pacman_conf, cfg["packages_file"], cfg["install_dir"])
        machine_id()
        fixperms(cfg["install_dir"])
        logging.info("Partitioning rock5b-split")
        rootfs_size = int(
            subprocess.check_output(["du", "-s", cfg["install_dir"]])
            .split()[0]
            .decode("utf-8")
        )
        img_size_b, ldev_b = makeimg(
            "150000", None, cfg["img_name"] + "_BOOT", cfg["img_backend"]
        )
        img_size_r, ldev_r = makeimg(
            rootfs_size, cfg["fs"], cfg["img_name"] + "_ROOTFS", cfg["img_backend"]
        )
        partition(ldev_b, None, img_size_b, cfg["partition_table_boot"])
        partition(
            ldev_r,
            cfg["fs"],
            img_size_r,
            cfg["partition_table_root"](img_size_r, cfg["fs"]),
            split=True,
        )
        if not os.path.exists(mnt_dir):
            os.mkdir(mnt_dir)
        subprocess.run("mount " + ldev_b + "p1 " + mnt_dir + "/boot", shell=True)
        copyfiles(cfg["install_dir"], mnt_dir, retainperms=True)
        create_extlinux_conf(mnt_dir, cfg["configtxt"], cfg["cmdline"], ldev_r)
        create_fstab(cfg["fs"], ldev_b, ldev_r)
        unmount(cfg["img_backend"], mnt_dir, ldev_b, ldev_r)
        cleanup(cfg["img_backend"])
        if args.no_compress:
            copyimage(cfg["img_name"] + "_BOOT")
            copyimage(cfg["img_name"] + "_ROOTFS")
        else:
            compressimage(cfg["img_name"] + "_BOOT")
            compressimage(cfg["img_name"] + "_ROOTFS")
        cleanup(cfg["work_dir"])

    elif cfg["device"] == "rock4c-plus":
        copyfiles(config_dir + "/alarmimg", cfg["install_dir"])
        fixperms(cfg["install_dir"])
        pacstrap_packages(pacman_conf, cfg["packages_file"], cfg["install_dir"])
        machine_id()
        fixperms(cfg["install_dir"])
        logging.info("Partitioning rock4c-plus")
        rootfs_size = int(
            subprocess.check_output(["du", "-s", cfg["install_dir"]])
            .split()[0]
            .decode("utf-8")
        )
        img_size, ldev = makeimg(
            rootfs_size, cfg["fs"], cfg["img_name"], cfg["img_backend"]
        )
        partition(
            ldev, cfg["fs"], img_size, cfg["partition_table"](img_size, cfg["fs"])
        )
        if not os.path.exists(mnt_dir):
            os.mkdir(mnt_dir)
        subprocess.run("mount " + ldev + "p1 " + mnt_dir + "/boot", shell=True)
        subprocess.run(
            [
                "cp",
                "-v",
                config_dir + "/nvram.txt",
                cfg["install_dir"] + "/usr/lib/firmware/brcm/brcmfmac43455-sdio.txt",
            ]
        )
        copyfiles(cfg["install_dir"], mnt_dir, retainperms=True)
        create_extlinux_conf(mnt_dir, cfg["configtxt"], cfg["cmdline"], ldev)
        create_fstab(cfg["fs"], ldev)
        unmount(cfg["img_backend"], mnt_dir, ldev)
        cleanup(cfg["img_backend"])
        if args.no_compress:
            copyimage(cfg["img_name"])
        else:
            compressimage(cfg["img_name"])
        cleanup(cfg["work_dir"])
    elif cfg["device"] == "vim4":
        copyfiles(config_dir + "/alarmimg", cfg["install_dir"])
        fixperms(cfg["install_dir"])
        pacstrap_packages(pacman_conf, cfg["packages_file"], cfg["install_dir"])
        machine_id()
        fixperms(cfg["install_dir"])
        logging.info("Partitioning vim4")
        rootfs_size = int(
            subprocess.check_output(["du", "-s", cfg["install_dir"]])
            .split()[0]
            .decode("utf-8")
        )
        img_size, ldev = makeimg(
            rootfs_size, cfg["fs"], cfg["img_name"], cfg["img_backend"]
        )
        partition(
            ldev,
            cfg["fs"],
            img_size,
            cfg["partition_table"](img_size, cfg["fs"]),
        )
        if not os.path.exists(mnt_dir + "/boot"):
            os.mkdir(mnt_dir + "/boot")
        subprocess.run("mount " + ldev + "p1 " + mnt_dir + "/boot", shell=True)
        copyfiles(cfg["install_dir"], mnt_dir, retainperms=True)
        create_extlinux_conf(mnt_dir, cfg["configtxt"], cfg["cmdline"], ldev)
        create_fstab(cfg["fs"], ldev, simple_vfat=True, no_discard=True)
        unmount(cfg["img_backend"], mnt_dir, ldev)
        cleanup(cfg["img_backend"])
        if args.no_compress:
            print(
                "The image will not have metadata for owowoo applied since it's not compressed!"
            )
            copyimage(cfg["img_name"])
        else:
            compressimage(cfg["img_name"])
            subprocess.check_output(["chmod", "+x", config_dir + "/xze"])
            subprocess.check_output(
                [
                    config_dir + "/xze",
                    cfg["out_dir"] + "/" + cfg["img_name"] + ".img.xz",
                    "--meta",
                    "label=BredOS",
                    "builder=BredOS",
                    "date=" + time.ctime().replace("  ", " "),
                    "match=BOARD=VIM4",
                    "link=https://bredos.org/",
                    "duration=400",
                    "desc=Vim 4 BredOS Plasma edition v" + cfg["edition"],
                ]
            )
        cleanup(cfg["work_dir"])
    elif cfg["device"] == "cpi4":
        copyfiles(config_dir + "/alarmimg", cfg["install_dir"])
        fixperms(cfg["install_dir"])
        pacstrap_packages(pacman_conf, cfg["packages_file"], cfg["install_dir"])
        machine_id()
        fixperms(cfg["install_dir"])
        logging.info("Partitioning cpi4")
        rootfs_size = int(
            subprocess.check_output(["du", "-s", cfg["install_dir"]])
            .split()[0]
            .decode("utf-8")
        )
        img_size, ldev = makeimg(rootfs_size, fs, cfg["img_name"], cfg["img_backend"])
        partition(
            ldev, cfg["fs"], img_size, cfg["partition_table"](img_size, cfg["fs"])
        )
        if not os.path.exists(mnt_dir):
            os.mkdir(mnt_dir)
        subprocess.run("mount " + ldev + "p1 " + mnt_dir + "/boot", shell=True)
        copyfiles(cfg["install_dir"], mnt_dir, retainperms=True)
        copyfiles(cfg["install_dir"], mnt_dir, retainperms=True)
        create_extlinux_conf(mnt_dir, cfg["configtxt"], cfg["cmdline"], ldev)
        create_fstab(cfg["fs"], ldev)
        unmount(cfg["img_backend"], mnt_dir, ldev)
        cleanup(cfg["img_backend"])
        if args.no_compress:
            copyimage(cfg["img_name"])
        else:
            compressimage(cfg["img_name"])
        cleanup(cfg["work_dir"])
    elif cfg["device"] == "edge2":
        # copyfiles(config_dir+ "/alarmimg",cfg["install_dir"])
        # pacstrap_packages(pacman_conf, cfg["packages_file"], cfg["install_dir"])
        # subprocess.run(' '.join(["rm", "-rf",
        #     cfg["install_dir"] + "/etc/machine-id",
        #     cfg["install_dir"] + "/var/lib/dbus/machine-id"]),shell=True)
        # fixperms(cfg["install_dir"])
        # logging.info("Partitioning edge 2")
        # rootfs_size=int(subprocess.check_output(["du", "-s", cfg["install_dir"]]).split()[0].decode("utf-8"))
        # img_size,ldev = makeimg(rootfs_size,fs,cfg["img_name"],img_backend)
        # partition_edge2(ldev, fs, img_size)
        # logging.info("Partitioned edge 2 successfully")
        # if not os.path.exists(mnt_dir):
        #     os.mkdir(mnt_dir)
        # subprocess.run("mount " + ldev+"p1 " + mnt_dir + "/boot",shell=True)
        # copyfiles(cfg["install_dir"], mnt_dir,retainperms=True)
        # create_extlinux_conf()
        # create_fstab(cfg["fs"], ldev)
        # cleanup(cfg["img_backend"])
        if args.no_compress:
            copyimage(cfg["img_name"])
        else:
            compressimage(cfg["img_name"])
        cleanup(cfg["work_dir"])


def handler(signal_received, frame):
    # Handle any cleanup here
    logging.error("SIGINT or CTRL-C detected. Exiting gracefully")
    try:
        subprocess.run(["umount", "-R", mnt_dir])
    except:
        pass
    try:
        subprocess.run("umount -R " + cfg["install_dir"] + "/*", shell=True)
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


def next_loop() -> str:
    return subprocess.check_output(["losetup", "-f"]).decode("utf-8").strip("\n")


if __name__ == "__main__":
    cfg = verify_config()
    if cfg["img_backend"] == "loop":
        next_loop()
    elif cfg["img_backend"] == "qemu-nbd":
        subprocess.run(["modprobe", "nbd"])
        ldev = "/dev/nbd2"
    signal(SIGINT, handler)
    signal(SIGTERM, handler)
    # get start time
    start_time = time.time()
    main()
    # get end time
    end_time = time.time()
    # calculate total time taken convert to human readable format
    total_time = time.strftime("%H:%M:%S", time.gmtime(end_time - start_time))
    logging.info("Total time taken: " + total_time)
