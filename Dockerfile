FROM quay.io/fedora/fedora:44

WORKDIR /tmp

COPY . .

RUN INSTALL_PKGS="python3 python3-devel python3-setuptools python3-pip python3-virtualenv nss_wrapper \
        gettext rpm wget tar which openssl krb5-devel redhat-rpm-config libcurl-devel rpm-devel \
        httpd httpd-devel atlas-devel gcc-gfortran libffi-devel gcc libffi-devel libtool-ltdl enchant \
        git wget krb5-workstation krb5-libs openssl-devel nss_wrapper koji git fedora-messaging python3-rpm \
        bodhi-client" && \
    dnf -y --setopt=tsflags=nodocs install $INSTALL_PKGS && \
    dnf -y clean all --enablerepo='*'

# Copy in the Koji config files rather than installing fedora-packager because
# it pulls in far too many dependencies.
COPY koji_config/fedora.conf /etc/koji.conf.d/fedora.conf
COPY koji_config/stg.conf /etc/koji.conf.d/stg.conf

RUN mkdir -p /etc/elnbuildsync && \
    chgrp -R 0 /etc/elnbuildsync && \
    chmod -R g=u /etc/elnbuildsync

USER 1001
EXPOSE 8080

ENTRYPOINT ["/tmp/run.sh"]
