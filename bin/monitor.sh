#!/bin/bash
# initscript for the rhubarbe monitoring service
# to be run in faraday

type rhubarbe >& /dev/null || {
    echo "$0 requires rhubarbe to be (pip3-)installed"
    exit 1
    }

function locate_pid() {
    key=$1; shift
    pids=$(pgrep -f $key)
    [ -n "$pids" ] && ps $pids | egrep -v 'PID|stop' | awk '{print $1;}'
}

function start() {
    while getopts "dc:" opt; do
	case $opt in
	    d)
		debug="--debug" ;;
	    c)
		cycle="--cycle $OPTARG";;
	esac
    done
    shift $((OPTIND-1))

    rhubarbe-monitor -a $debug $cycle &
}

function stop() {
    # kill sh processes first
    killed=""
    pids=$(locate_pid rhubarbe-monitor)
    [ -n "$pids" ] && { kill $pids; killed="$killed $pids"; }
    pids=$(locate_pid monitor.py)
    [ -n "$pids" ] && { kill $pids; killed="$killed $pids"; }
    if [ -z "$killed" ]; then    
	echo nothing to stop
    else
	echo stopped
    fi
}

function status() {
    pids="$(locate_pid rhubarbe-monitor)"
    if [ -z "$pids" ]; then
	echo not running
    else
	ps $pids
    fi
}

# One can run
# monitor.sh start -v 10 23

function main() {
    verb=$1; shift
    case $verb in
	start) start "$@" ;;
	stop) stop ;;
	status) status ;;
	restart) echo "please run $0 stop; $0 start" ;;
	*) echo No such verb $1; exit 1 ;;
    esac
}

main "$@"
