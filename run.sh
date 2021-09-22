#!/bin/bash
set -eo pipefail

export KRB5CCNAME=FILE:/tmp/tgt

# This method should be executed manually each time user login to the pod

export passwd_output_dir="/tmp"
export USER_ID=$(id -u)
export GROUP_ID=$(id -g)
envsubst < /passwd.template > ${passwd_output_dir}/passwd
export LD_PRELOAD=libnss_wrapper.so
export NSS_WRAPPER_PASSWD=${passwd_output_dir}/passwd
export NSS_WRAPPER_GROUP=/etc/group

python3 --version

virtualenv .venv 
. .venv/bin/activate
pip install --upgrade pip
pip install requests
ln -sf /etc/pki/tls/certs/ca-bundle.crt $(python3 -c 'import requests; print(requests.certs.where())')

pip install -r test-requirements.txt
python3 distrobuildsync --version

# Added for debug
sleep 10h
