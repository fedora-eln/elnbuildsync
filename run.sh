#!/bin/bash
set -eo pipefail

export KRB5CCNAME=FILE:/tmp/tgt

echo "Running scheduler"
(while true; do kinit -k -t /keytab/distrobaker.keytab distrobuildsync-eln/jenkins-continuous-infra.apps.ci.centos.org@FEDORAPROJECT.ORG; sleep 55m; done) &

sleep 3

# This method should be executed manually each time user login to the pod
export passwd_output_dir="/tmp"
export USER_ID=$(id -u)
export GROUP_ID=$(id -g)
envsubst < /passwd.template > ${passwd_output_dir}/passwd
export LD_PRELOAD=libnss_wrapper.so
export NSS_WRAPPER_PASSWD=${passwd_output_dir}/passwd
export NSS_WRAPPER_GROUP=/etc/group

if [ -z $DBS_CFG_BRANCH ]; then
export CONFIG_URL="https://gitlab.com/redhat/centos-stream/ci-cd/distrosync/distrobuildsync-config.git"
else
export CONFIG_URL="https://gitlab.com/redhat/centos-stream/ci-cd/distrosync/distrobuildsync-config.git#$DBS_CFG_BRANCH"
fi

echo "EXECUTING klist"
klist

# echo "EXECUTING ssh to the pkgs.devel.redhat.com"
# ssh pkgs.devel.redhat.com

python3 --version

echo "Activation virtualenv"
virtualenv --system-site-packages .venv
. .venv/bin/activate
pip install --upgrade pip
# htmlmin requires the cgi module which was removed from stdlib, but hasn't been updated to declare it
pip install legacy-cgi
pip install --no-build-isolation htmlmin
pip install -r requirements.txt

export FEDORA_MESSAGING_CONF=/etc/fedora-messaging/config.toml

python3 -c "from elnbuildsync import main; main()" \
  --config-url $CONFIG_URL \
  --db-pw-file /db_pw/ebs-db-pw \
  $EXTRA_ARGS

# Added for debug
# echo "Sleep 10 hours. Debugging..."
# sleep 10h
