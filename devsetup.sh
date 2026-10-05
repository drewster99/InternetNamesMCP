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
    exit 1
fi

# Every path below is relative to the current directory, so sourcing this file from
# anywhere else would clean or install into some other directory's .venv.
# zsh leaves BASH_SOURCE empty, and its $0 is only the script path while FUNCTION_ARGZERO
# is set, so ask zsh directly for the file being sourced.
if [ -n "${ZSH_VERSION-}" ]; then
    _devsetup_script="${(%):-%x}"
else
    _devsetup_script="${BASH_SOURCE[0]}"
fi
# -ef alone would also pass for a symlink to this file named devsetup.sh in another directory.
if [ -L ./devsetup.sh ] || [ ! ./devsetup.sh -ef "$_devsetup_script" ]; then
    echo "devsetup.sh: error: must be sourced from the directory that contains it, not from $(pwd -P)" 1>&2
    unset _devsetup_script
    return 1
fi
unset _devsetup_script

usage()
{
    echo "usage: source $BASH_SOURCE [--clean] [--setup] [--help]" 1>&2
}

showHelp()
{
    echo "Sets up development / testing environment." 1>&2
    echo "" 1>&2
    echo "Options:" 1>&2
    echo "--help        Shows this message" 1>&2
    echo "--clean       Deletes python cache and virtual environment"
    echo "--setup       Sets up python venv and dependencies for development and testing (default if no args)"
    echo "" 1>&2
    echo "Multiple options can be combined, e.g.: source $BASH_SOURCE --clean --setup" 1>&2
}

doSetup=0
doClean=0

if [ $# -eq 0 ]; then
    doSetup=1
else
    for arg in "$@"; do
        if [ "$arg" = "--help" ]; then
            showHelp
            usage
            return 0
        elif [ "$arg" = "--clean" ]; then
            doClean=1
        elif [ "$arg" = "--setup" ]; then
            doSetup=1
        else
            echo "`basename $BASH_SOURCE`: error: unknown argument \"$arg\"" 1>&2
            usage
            return 1
        fi
    done
fi

# Clean if requested
if [ $doClean -eq 1 ]; then
    echo "Cleaning..."
    # Playwright browsers live in a cache shared by every Playwright install on this machine,
    # so clean leaves them alone. Once .venv is gone this project's registration in that cache
    # dangles, and Playwright itself deletes browsers nothing else uses during the next
    # `playwright install` run by any project. Until then, a .venv rebuilt at the same path
    # makes that registration valid again and reuses the browser instead of downloading it.
    if [[ -n "$VIRTUAL_ENV" ]]; then
        echo "Deactivating virtual environment: $VIRTUAL_ENV"
        type deactivate > /dev/null 2>&1
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
    if [ -d src/internet_names_mcp/__pycache__ ]; then
        echo "Deleting src/internet_names_mcp/__pycache__"
        rm -rf src/internet_names_mcp/__pycache__
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
    python3 -m venv .venv || return 1
fi

# activate virtual environment
echo source .venv/bin/activate
# Without an active venv, the pip below would install into whatever Python is first on PATH.
source .venv/bin/activate || return 1

# Install dependencies (including dev dependencies for build/publish tools)
echo 'pip install -e ".[dev]"'
pip install -e ".[dev]"
