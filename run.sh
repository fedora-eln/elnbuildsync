#!/bin/bash
set -eo pipefail

export TMPDIR=/var/tmp
export KRB5CCNAME=FILE:${TMPDIR}/tgt

echo "Running scheduler"
(while true; do kinit -k -t /keytab/distrobaker.keytab eln-buildsync@FEDORAPROJECT.ORG; sleep 55m; done) &

sleep 3

# This method should be executed manually each time user login to the pod
export passwd_output_dir=${TMPDIR}
export USER_ID=$(id -u)
export GROUP_ID=$(id -g)
envsubst < /passwd.template > ${passwd_output_dir}/passwd
export LD_PRELOAD=libnss_wrapper.so
export NSS_WRAPPER_PASSWD=${passwd_output_dir}/passwd
export NSS_WRAPPER_GROUP=/etc/group

if [ -z "${EBS_CONFIG_URL}" ]; then
  if [ -z "${EBS_CONFIG_BRANCH}" ]; then
    export EBS_CONFIG_URL="https://gitlab.com/redhat/centos-stream/ci-cd/distrosync/distrobuildsync-config.git"
  else
    export EBS_CONFIG_URL="https://gitlab.com/redhat/centos-stream/ci-cd/distrosync/distrobuildsync-config.git#${EBS_CONFIG_BRANCH}"
  fi
fi

echo "EXECUTING klist"
klist

# echo "EXECUTING ssh to the pkgs.devel.redhat.com"
# ssh pkgs.devel.redhat.com

python3 --version

echo "Activation virtualenv"
virtualenv --system-site-packages ${TMPDIR}/.venv
. ${TMPDIR}/.venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

export FEDORA_MESSAGING_CONF=/etc/fedora-messaging/config.toml

python3 -c "from elnbuildsync import main; main()" \
  --config-url $EBS_CONFIG_URL \
  --db-pw-file /db_pw/ebs-db-pw \
  $EXTRA_ARGS

# Added for debug
# echo "Sleep 10 hours. Debugging..."
# sleep 10h
