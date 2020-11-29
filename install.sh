#!/bin/bash

# Ensure that the script fails if something failed
set -e

LOG_DIR=$PWD/install_logs
mkdir -p $LOG_DIR


prepare_sudo_install_flag=0
while getopts 'p' opt; do
    case $opt in
        p) prepare_sudo_install_flag=1 ;;
        *) echo 'Error in command line parsing' >&2
           exit 1
    esac
done
shift "$(( OPTIND - 1 ))"

## If option -p is set, also run the sudo
if [ "$prepare_sudo_install_flag" -eq 1 ]; then
    echo "Running preparation sudo apt install and opam init:"
    sudo apt update > $LOG_DIR/apt_update.log
    sudo apt install -y libtool m4 automake opam pkg-config libffi-dev python3.8 python3-pip > $LOG_DIR/apt_install.log
    yes | opam init > $LOG_DIR/opam_init.log
else
    echo "Requires libtool, m4, automake, opam, pkg-config, libffi-dev, python3.8, pip for python3"
    echo "Ensure that you have them by running:"
    echo "  sudo apt install libtool m4 automake opam pkg-config libffi-dev python3.8 python3-pip"
    echo "  opam init"
    echo "Press 'y' if you have these dependencies installed."
    while : ; do
        read -n 1 k <&1
        if [[ $k = y ]] ; then
            echo "Proceeding..."
            break
        fi
    done
fi


# Build the parser (requires libtool, m4, automake, opam)
echo "Building parser..."
eval $(opam config env)
cd parser
make opam-dependencies > $LOG_DIR/make_opam_dependencies.log
make libdash > $LOG_DIR/make_libdash.log
make > $LOG_DIR/make.log
cd ../

echo "Building runtime..."
# Build runtime tools: eager, split
cd evaluation/tools/
make > $LOG_DIR/make.log
cd ../../

# Install python3.8 dependencies
python3.8 -m pip install jsonpickle > $LOG_DIR/pip_install_jsonpickle.log
python3.8 -m pip install -U PyYAML > $LOG_DIR/pip_install_pyyaml.log

# Generate inputs
echo "Generating input files"
cd evaluation/scripts/input
./gen.sh
cd ../../../

# Export necessary environment variables
export PASH_TOP=$PWD
export PASH_PARSER=${PASH_TOP}/parser/parse_to_json.native

## This is necessary for the parser to link to libdash
export LD_LIBRARY_PATH="$LD_LIBRARY_PATH:/usr/local/lib/"