#!/bin/bash
# Calculate the max available buffer size, which is the lesser
# of 95% of actual size or DEFAULTSIZE

DEFAULTSIZE=500000000
MINSIZE=134217728 # 128 MB
MAXSIZE=1099511627776 # 1 TB

if [ -d /data/ ] ; then ACTUALSIZE=`df -B1 /data/ --output="size" | grep -v 1B-blocks`; fi

if [ -z "${ACTUALSIZE+xxx}" ]; then ACTUALSIZE=${DEFAULTSIZE}; fi
if [ -z "$ACTUALSIZE" ] && [ "${ACTUALSIZE+xxx}" = "xxx" ]; then ACTUALSIZE=${DEFAULTSIZE}; fi

if [ "${ACTUALSIZE}" -gt "${MAXSIZE}" ]; then
  ACTUALSIZE=${MAXSIZE}
fi

SIZE=$((${ACTUALSIZE}*95/100))

if [ "${ACTUALSIZE}" -lt "${MINSIZE}" ]; then
  SIZE=${MINSIZE}
fi

MACHINE_ID=`cat /etc/machine-id`

cat >/run/vector/ENV<<EOF
# /run/vector/ENV
# This file is overwritten each time the service starts, do not add variables manually.
VECTOR_REMOTE_BUFFER_MAXSIZE=${SIZE:-500000000}
VECTOR_MACHINE_ID=${MACHINE_ID:-00000000000000000000000000000000}
EOF
