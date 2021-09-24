#!/bin/bash
set -eo pipefail

export KRB5CCNAME=FILE:/tmp/tgt

echo "Running scheduler"
(while true; do kinit -k -t /keytab/distrobaker.keytab distrobaker/distrobaker.osci.redhat.com@REDHAT.COM; sleep 1h; done) &

sleep 3

# This method should be executed manually each time user login to the pod
# generate_container_user() {

export passwd_output_dir="/tmp"
export USER_ID=$(id -u)
export GROUP_ID=$(id -g)
envsubst < /passwd.template > ${passwd_output_dir}/passwd
export LD_PRELOAD=libnss_wrapper.so
export NSS_WRAPPER_PASSWD=${passwd_output_dir}/passwd
export NSS_WRAPPER_GROUP=/etc/group

# }

# generate_container_user

echo "EXECUTING klist"
klist

# echo "EXECUTING ssh to the pkgs.devel.redhat.com"
# ssh pkgs.devel.redhat.com


python3 --version

echo "Activation virtualenv"
virtualenv .venv 
. .venv/bin/activate
pip install --upgrade pip
pip install requests
ln -sf /etc/pki/tls/certs/ca-bundle.crt $(python3 -c 'import requests; print(requests.certs.where())')

pip install -r test-requirements.txt
python3 distrobuildsync --help

# Added for debug
echo "Sleep 10 hours. Debugging..."
sleep 10h
