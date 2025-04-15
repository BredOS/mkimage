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
    "-ff", "--fast-forward", help="Compress very briefly .xz", action="store_true"
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

    import profiledef  # type: ignore

    cfg["arch"] = profiledef.arch
    cfg["cmdline"] = profiledef.cmdline
    cfg["configtxt"] = profiledef.configtxt
    try:
        cfg["configtxt_suffix"] = profiledef.configtxt_suffix
    except AttributeError:
        cfg["configtxt_suffix"] = None
    cfg["edition"] = profiledef.edition
    cfg["fs"] = profiledef.fs
    cfg["img_backend"] = profiledef.img_backend
    cfg["img_name"] = profiledef.img_name
    cfg["img_type"] = profiledef.img_type
    cfg["img_version"] = profiledef.img_version
    cfg["perms"] = profiledef.perms
    cfg["mkcmds"] = profiledef.mkcmds
    try:
        cfg["grubcmdl"] = profiledef.grubcmdl
        cfg["grubdtb"] = profiledef.grubdtb
    except AttributeError:
        pass
    try:
        cfg["part_type"] = "gpt" if profiledef.use_gpt else "msdos"
    except AttributeError:
        cfg["part_type"] = "gpt"
    try:
        cfg["boot_set_esp"] = profiledef.boot_set_esp
    except AttributeError:
        cfg["boot_set_esp"] = True
    try:
        cfg["partition_table"] = profiledef.partition_table
    except AttributeError:
        cfg["partition_table_boot"] = profiledef.partition_table_boot
        cfg["partition_table_root"] = profiledef.partition_table_root
    try:
        cfg["partition_suffix"] = profiledef.partition_suffix
    except AttributeError:
        cfg["partition_suffix"] = lambda config_dir, disk: []
    try:
        cfg["partition_prefix"] = profiledef.partition_prefix
    except AttributeError:
        cfg["partition_prefix"] = lambda config_dir, disk: []
    try:
        cfg["has_uefi"] = profiledef.has_uefi
    except AttributeError:
        cfg["has_uefi"] = False
    cfg["config_dir"] = config_dir
    cfg["work_dir"] = work_dir

    packages_file = config_dir + "packages." + cfg["arch"]
    cfg["packages_file"] = packages_file

    if cfg["arch"] not in ["aarch64", "armv7h", "riscv64"]:
        logging.error("Arch incompatible. Use aarch64, armv7h or riscv64")
        exit(1)
    if not cfg["img_name"]:
        logging.error("Image name not set")
        exit(1)
    if not cfg["img_version"]:
        logging.error("Image version not set")
        exit(1)

    install_dir = work_dir + ("/" if not work_dir.endswith("/") else "") + cfg["arch"]
    cfg["install_dir"] = install_dir
    subprocess.run(["mkdir", "-p", install_dir])

    if cfg["fs"] not in ["ext4", "btrfs"]:
        logging.error("Filesystem not supported use ext4 or btrfs")
        exit(1)
    if not os.path.isfile(packages_file):
        logging.error(
            "packages file doesnt exist create the file packages." + cfg["arch"]
        )
        exit(1)
    if cfg["img_type"] not in ["image", "rootfs"]:
        logging.error("Image type not supported use image or rootfs ")
        exit(1)
    if cfg["img_backend"] not in ["loop"]:
        logging.error("Image backend not supported use loop")
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


def get_fsline(device) -> str:  # type: ignore
    fl = subprocess.check_output(["blkid", device]).decode("utf-8")
    spl = fl.split(" ")
    for i in spl:
        if i.startswith("UUID="):
            return str(i.replace('"', ""))


def get_parttype(device):
    fl = subprocess.check_output(["blkid", device]).decode("utf-8")
    spl = fl.split(" ")
    for i in spl:
        if i.startswith("TYPE="):
            return i[6:-1]


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
    if not fs == "btrfs":
        img_size = size + int(1100000)
    else:
        img_size = size

    logging.info("Creating image file " + img_name + ".img")
    subprocess.run(
        [
            "fallocate",
            "-l",
            str(img_size) + "K",
            work_dir + "/" + img_name + ".img",
        ]
    )

    subprocess.run(["modprobe", "loop"])
    logging.info(
        "Attaching image file " + img_name + ".img to loop device " + next_loop()
    )
    ldev = next_loop()
    subprocess.run(["losetup", ldev, work_dir + "/" + img_name + ".img"])

    logging.info("Image file created")
    return img_size, ldev


def partition(disk, fs, img_size, partition_table, split=False, has_uefi=False):
    table = [["Partition", "Start", "End", "Size", "Filesystem"]]
    if has_uefi:
        prtd_cmd = [
        "parted",
        "--script",
        disk,
        "--align",
        "optimal",
    ]
    else:
        prtd_cmd = [
            "parted",
            "--script",
            disk,
            "--align",
            "optimal",
            "mklabel",
            cfg["part_type"],
        ]
    ld_partition_table = partition_table

    for i in ld_partition_table.keys():
        table.append([i] + partition_table[i])
        if partition_table[i][3] == "fat32":
            if has_uefi:
                part_num = str(2)
            else:
                part_num = str(1)
            prtd_cmd += [
                "mkpart",
                "primary",
                "fat32",
                ld_partition_table[i][0],
                ld_partition_table[i][1],
                "set",
                part_num,
                "boot",
                "on",
            ]
            if cfg["boot_set_esp"]:
                prtd_cmd += ["set", part_num, "esp", "on"]
        elif ld_partition_table[i][3] == "NONE":
            pass
        else:
            prtd_cmd += [
                "mkpart",
                "primary",
                partition_table[i][3],
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

    if not split:
        for i in cfg["partition_prefix"](config_dir, disk):
            subprocess.run(i)

    logging.info(f"Full command: {prtd_cmd}")
    subprocess.run(prtd_cmd)

    if not split:
        for i in cfg["partition_suffix"](config_dir, disk):
            subprocess.run(i)

    if not os.path.exists(mnt_dir):
        os.mkdir(mnt_dir)

    idf = "p3" if has_uefi else ("p2" if not split else "p1")

    if fs == "ext4":
        subprocess.run("mkfs.ext4 -F -L PRIMARY " + disk + idf, shell=True)
        subprocess.run("mount " + disk + idf + " " + mnt_dir, shell=True)
        os.mkdir(mnt_dir + "/boot")
        if has_uefi:
            os.mkdir(mnt_dir + "/boot/efi")
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
        if has_uefi:
            os.mkdir(mnt_dir + "/boot/efi")

    logging.info("Partitioned successfully")


def create_fstab(fs, ldev, ldev_alt=None, simple_vfat=False) -> None:
    if cfg["has_uefi"]:
        id1 = get_fsline(ldev + "p2")
        id2 = get_fsline(ldev + "p3")
    else:
        id1 = get_fsline(ldev + "p1")
        id2 = get_fsline((ldev_alt + "p1") if ldev_alt is not None else (ldev + "p2"))

    if fs == "ext4":
        with open(mnt_dir + "/etc/fstab", "a") as f:
            f.write(id1 + " / ext4 defaults 0 0\n")
    else:
        with open(mnt_dir + "/etc/fstab", "a") as f:
            f.write(
                id2
                + " /"
                + 21 * " "
                + "btrfs rw,relatime,ssd"
                + ",compress=zstd,space_cache=v2,subvol=/@ 0 0\n"
            )
            f.write(
                id2
                + " /.snapshots"
                + 11 * " "
                + "btrfs rw,relatime,ssd,discard=async,compress=zstd,"
                + "space_cache=v2,subvol=/@.snapshots 0 0\n"
            )
            f.write(
                id2
                + " /home"
                + 17 * " "
                + "btrfs rw,relatime,ssd,discard=async,compress=zstd,"
                + "space_cache=v2,subvol=/@home 0 0\n"
            )
            f.write(
                id2
                + " /var/cache/pacman/pkg btrfs rw,relatime,ssd,discard=async,"
                + "space_cache=v2,subvol=/@pkg 0 0\n"
            )
            f.write(
                id2
                + " /var/log"
                + 14 * " "
                + "btrfs rw,relatime,ssd,discard=async,compress=zstd,"
                + "space_cache=v2,subvol=/@log 0 0\n"
            )
    with open(mnt_dir + "/etc/fstab", "a") as f:
        if cfg["has_uefi"]:
            boot_fs = get_parttype(ldev + "p2")
            mount_point = "/boot/efi"
        else:
            boot_fs = get_parttype(ldev + "p1")
            mount_point = "/boot"
        if boot_fs == "vfat":
            f.write(
                id1
                + ((28 * " ") if len(id1) == 14 else "")
                + mount_point
                + 17 * " "
                + boot_fs
                + (" " if fs == "btrfs" else "")
                + "  rw,relatime,fmask=0022,dmask=0022,codepage=437,"
                + ("iocharset=ascii," if not simple_vfat else "")
                + "shortname=mixed,utf8,errors=remount-ro 0 2\n"
            )
        else:
            f.write(
                (get_fsline(ldev + "p2") if not cfg["has_uefi"] else get_fsline(ldev + "p1"))
                + mount_point
                + 17 * " "
                + boot_fs
                + (" " if fs == "btrfs" else "")
                + " rw,relatime,errors=remount-ro 0 2\n"
            )


def copy_skel_to_users() -> None:
    non_root_users = []

    try:
        with open(cfg["install_dir"] + "/etc/passwd", "r") as passwd_file:
            lines = passwd_file.readlines()

        for line in lines:
            parts = line.split(":")
            username = parts[0]
            uid = int(parts[2])

            if (
                uid != 0 and uid > 1000 and uid < 2000
            ):  # Check if the user ID is not root (UID 0)
                non_root_users.append(username)

    except FileNotFoundError:
        print("Error: No passwd file not found.")

    for user in non_root_users:
        logging.info("Copying skel to " + user)
        subprocess.run(["mkdir", "-p", cfg["install_dir"] + "/home/" + user])
        subprocess.run(
            "cp -r "
            + cfg["install_dir"]
            + "/etc/skel/. "
            + cfg["install_dir"]
            + "/home/"
            + user,
            shell=True,
        )

    with open(cfg["install_dir"] + "/version", "w") as f:
        f.write("BredOS " + cfg["img_version"] + "\n")


def create_extlinux_conf(mnt_dir, configtxt, cmdline, ldev) -> None:
    if not os.path.exists(mnt_dir + "/boot/extlinux"):
        os.mkdir(mnt_dir + "/boot/extlinux")
        subprocess.run(["touch", mnt_dir + "/boot/extlinux/extlinux.conf"])
    with open(mnt_dir + "/boot/extlinux/extlinux.conf", "w") as f:
        f.write(configtxt)
        # add append root=UUID=... + cmdline
        if "partition_table_root" in cfg:
            root_uuid = get_fsline(ldev + "p1")
        else:
            root_uuid = get_fsline(ldev + "p2")
        f.write("    append root=" + root_uuid + " " + cmdline)
        if cfg["configtxt_suffix"] is not None:
            f.write(cfg["configtxt_suffix"])


def run_chroot_cmd(work_dir: str, cmd: list) -> None:
    subprocess.run(["arch-chroot", work_dir] + cmd)


def grub_install(mnt_dir: str, arch: str ="arm64-efi") -> None:
    grubfile = open(mnt_dir + "/etc/default/grub")
    grubconf = grubfile.read()
    grubfile.close()
    grubcmdl = cfg["grubcmdl"]
    grubdtb = cfg["grubdtb"]
    grubconf = grubconf.replace('GRUB_CMDLINE_LINUX_DEFAULT="loglevel=3 quiet"', f'GRUB_CMDLINE_LINUX_DEFAULT="{grubcmdl}"')
    if grubdtb:
        grubconf = grubconf.replace('# GRUB_DTB="path_to_dtb_file"', f'GRUB_DTB="{grubdtb}"')
    grubfile = open(mnt_dir + "/etc/default/grub", "w")
    grubfile.write(grubconf)
    grubfile.close()
    run_chroot_cmd(mnt_dir, ["grub-install", f"--target={arch}", "--efi-directory=/boot/efi", "--removable", "--bootloader-id=BredOS"])
    if not os.path.exists(mnt_dir + "/boot/grub"):
        os.mkdir(mnt_dir + "/boot/grub")
    run_chroot_cmd(mnt_dir, ["grub-mkconfig", "-o", "/boot/grub/grub.cfg"])


def cleanup(work_dir: str) -> None:
    logging.info("Cleaning up")
    subprocess.run(["rm", "-rf", work_dir])


def unmount(img_backend: str, mnt_dir: str, ldev: str, ldev_alt: str = None) -> None:
    logging.info("Unmounting!")
    subprocess.run(["umount", "-R", mnt_dir])
    if img_backend == "loop":
        subprocess.run(["losetup", "-d", ldev])
        if ldev_alt is not None:
            subprocess.run(["losetup", "-d", ldev_alt])


def compressimage(img_name: str) -> None:
    logging.info("Compressing " + img_name + ".img")
    subprocess.run(
        [
            "xz",
            "-k",
            "-5" if not args.fast_forward else "-1",
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


def copyimage(img_name: str) -> None:
    logging.info("Copying " + img_name + ".img")
    # Move the image to the correct output directory
    subprocess.run(
        ["cp", work_dir + "/" + img_name + ".img", out_dir + "/" + img_name + ".img"]
    )
    subprocess.run(["chmod", "-R", "777", out_dir])
    logging.info("Copied " + img_name + ".img")


def copyfiles(ot: str, to: str, retainperms=False) -> None:
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
    logging.info("               Filesystem:   " + cfg["fs"])
    logging.info("               Image type:   " + cfg["img_type"])
    logging.info("          Image file name:   " + cfg["img_name"])
    logging.info("            Packages File:   " + cfg["packages_file"])
    exec(cfg["mkcmds"])


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
        if cfg["img_backend"] == "loop":
            subprocess.run(["losetup", "-d", ldev])
    except:
        pass
    exit(0)


def next_loop() -> str:
    return subprocess.check_output(["losetup", "-f"]).decode("utf-8").strip("\n")


if __name__ == "__main__":
    cfg = verify_config()
    if cfg["img_backend"] == "loop":
        ldev = next_loop()
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
