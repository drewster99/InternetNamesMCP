#!/bin/bash

# Check if we are being "sourced", with ". devsetup.sh" or "source devsetup.sh"
if [ "$0" = "$BASH_SOURCE" ]; then
    echo "*********************************************" 1>&2
    echo "`basename $0` must be sourced, not executed" 1>&2
    echo "*********************************************" 1>&2
    echo "" 1>&2
    echo "To set up this environment, run it like this:" 1>&2
    echo "" 1>&2
    echo "    source \"$0\"" 1>&2
    return 1
fi

usage()
{
    echo "usage: source $BASH_SOURCE [--clean | --help]" 1>&2
}

showHelp()
{
    echo "Sets up development / testing environment." 1>&2
    echo "" 1>&2
    echo "Options:" 1>&2
    echo "--help        Shows this message" 1>&2
    echo "--clean       Deletes python cache and virtual environment (does not do setup)"
    echo "--setup       Sets up python venv and dependencies for development and testing (default)"
}

doSetup=1
doClean=0
if [ $# -gt 1 ]; then
    usage
    return 2
elif [ $# -eq 1 ]; then
    # By default, we won't do the setup if we got any arguments
    # unless the user specifically gave us --setup
    doSetup=0
    if [ "$1" = "--help" ]; then
        showHelp
        usage
        return 3
    elif [ "$1" = "--clean" ]; then
        doClean=1
    elif [ "$1" = "--setup" ]; then
        doSetup=1
    else
        echo "`basename $0`: error: unknown argument \"$1\"" 1>&2
        usage
        return 4
    fi
fi

# Clean if requested
if [ $doClean -eq 1 ]; then
    echo "Cleaning..."
    if [[ -n "$VIRTUAL_ENV" ]]; then
        echo "Deactivating virtual environment: $VIRTUAL_ENV"
        type deactivate 2>&1 > /dev/null
        if [ $? -eq 0 ]; then
            deactivate
        fi
    fi
    if [ -d .venv ]; then
        echo "Deleting virtual environment"
        rm -rf .venv
    fi
    if [ -d __pycache__ ]; then
        echo "Deleting __pycache__"
        rm -rf __pycache__
    fi
    if [ -f .rdap_bootstrap_cache.json ]; then
        echo "Deleting .rdap_bootstrap_cache.json"
        rm -f .rdap_bootstrap_cache.json
    fi
    echo "Cleaning complete."
fi

if [ $doSetup -eq 0 ]; then
    return 0
fi

# set up virtual environment
if [ ! -d .venv ]; then
    echo python3 -m venv .venv
    python3 -m venv .venv
fi

# activate virtual environment
echo source .venv/bin/activate
source .venv/bin/activate

# Install dependencies
echo pip install -e .
pip install -e .
