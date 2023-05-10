FROM quay.io/fedora/fedora:37

WORKDIR /tmp

COPY . .
COPY docker_files/ /tmp

RUN INSTALL_PKGS="python3 python3-devel python3-setuptools python3-pip python3-virtualenv nss_wrapper \
        gettext rpm wget tar which openssl krb5-devel redhat-rpm-config libcurl-devel rpm-devel \
        httpd httpd-devel atlas-devel gcc-gfortran libffi-devel gcc libffi-devel libtool-ltdl enchant \
        git wget krb5-workstation krb5-libs openssl-devel nss_wrapper koji git fedora-messaging python3-rpm \
        python3-htmlmin \
        /tmp/redhat-internal-cert-install-*.noarch.rpm" && \
    dnf -y --setopt=tsflags=nodocs install $INSTALL_PKGS && \
    dnf -y clean all --enablerepo='*' && \
    rpm -i /tmp/python3-brewkoji-*.noarch.rpm \
           /tmp/brewkoji-*.noarch.rpm && \
    rm -fr /tmp/python3-brewkoji-*.noarch.rpm \
           /tmp/brewkoji-*.noarch.rpm \
           /tmp/redhat-internal-cert-install-*.noarch.rpm

RUN mkdir /tmp/.ssh /centos_rsa /keytab /.cache && \
    touch /.gitconfig .gitconfig distrobaker_centos_id_rsa.pub

RUN mv /tmp/stream.conf /etc/koji.conf.d/stream.conf && \
    mv /tmp/passwd.template / && \
    mv /tmp/distrobaker_centos_id_rsa.pub /tmp/.ssh/

RUN mv /tmp/cacert.pem /etc/fedora-messaging/ && \
    mv /tmp/distrobuildsync-eln.crt /etc/fedora-messaging/ && \
    mv /tmp/distrobuildsync-eln.key /etc/fedora-messaging/ && \
    mv /tmp/config.toml /etc/fedora-messaging/config.toml

RUN mv /tmp/RH-IT-Root-CA.crt /etc/pki/ca-trust/source/anchors && \
    update-ca-trust extract

RUN chgrp -R 0   /tmp/.ssh /keytab /etc/pki/tls/certs/ .gitconfig /.cache && \
    chmod -R g=u /tmp/.ssh /keytab /etc/pki/tls/certs/ .gitconfig /.cache

RUN git config --global user.email "example@distrobaker.com" && \
    git config --global user.name "DistroBaker"

RUN cp /tmp/ssh_config /tmp/.ssh/ssh_config && \
    chmod 600 /tmp/.ssh/ssh_config && \
    rm -fr /etc/ssh/ssh_config && \
    cp /tmp/ssh_config /etc/ssh/ssh_config && \
    chmod 600 /tmp/.ssh/ssh_config

RUN ssh-keyscan -t rsa gitlab.com >> /tmp/.ssh/known_hosts

USER 1001

# Needs for kerberos
ENV KRB5CCNAME=FILE:/tmp/tgt

EXPOSE 8080
CMD ["/bin/sh", "run.sh"]
