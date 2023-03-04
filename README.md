# RebornOS ARM Image builder

This project is a Python script that automates the process of creating images for various Arm SBCs (Single Board Computers) using a board configuration file. The script currently supports the following SBCs:

- VIM 4
- Rock 5
- Orange Pi 5 (using Rock 5 board cfg)
- R58S (using Rock 5 board cfg)
- R58X (using Rock 5 board cfg)

# Requirements

To use this project, you will need:

- Python 3.6 or later installed on your system
- Git installed on your system
- Arch install scripts 
- These python libraries `argparse`, `prettytable`, `signal`
    

# Installation

To use this project, clone the repository to your local machine:

```bash
git clone https://github.com/RebornOS-Developers/mkimage
```
# Usage

To create an image for your chosen SBC, run the mkimage.py script with the appropriate arguments. The basic usage is:

```bash
./mkimage.py -w /tmp/work -o ./output -c <board-cfg>
```

Where:
- `-w`: the working directory to use
- `-o`: the output directory for the resulting image
- `-c`: the board configuration file to use
    
## **WARNING:** If your system has less than 16 GB of RAM, it is recommended to use a different directory for the working directory, as using `/tmp/work` can cause performance issues due to the limited space in the `/tmp` directory.

For example, to create an image for the Rock 5 board, using the lxqt-rock5b-image configuration, with a working directory of /tmp/work and an output directory of ./output, you would run:

```bash
./mkimage.py -w /tmp/work -o ./output -c ./lxqt-rock5b-image
```

# Board Configuration Files

Board configuration files contain the settings necessary to build an image for a specific SBC. These files are located in the configs directory of the project.

If you want to create an image for a different SBC, you will need to create a new configuration file for that board. You can use an existing configuration file as a starting point.

- alarmimg/: the directory containing the basic system files for the image
- fixperms.sh: a shell script for fixing file permissions on the system files
- idbloader.img and u-boot.itb: files needed for U-Boot on Rock 5 boards
- packages.aarch64: a list of packages to be installed on the image
- pacman.conf.aarch64: the configuration file used to create the image itself
- profiledef: a file containing basic information about the image, such as the version number, device, architecture, file system type, image name, image type, backend, and cmdline


# Contributing

If you find a bug or have a suggestion for improving this project, feel free to open an issue or submit a pull request. All contributions are welcome!

# License

This project is licensed under the GPL 3.0 License. See the LICENSE file for details.
