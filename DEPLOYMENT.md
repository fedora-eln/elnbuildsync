# How to release and deploy ELNBuildSync in the Fedora Infrastructure

## Step 1: Upstream release

### A) Determine the new release number

This project uses [Semantic Versioning](https://semver.org), aka X.Y.Z. Use
the following steps in order to determine how to increase the version number:

1. If no logical changes to the application have occurred, stop here and do
not perform a new release. Documentation and CI changes do not justify a new
release version.
1. If any changes have been made to the logic of ELNBuildSync since the
previous upstream release, increase the Patch (Z) release value.
1. If any of the changes have added a new user-visible feature, set the Patch
(Z) release value to zero (0) and increase the Minor (Y) release value
by one.
1. If any of the changes have removed or *substantially redesigned* a
user-visible feature, set both the Minor (Y) release value and the Patch (Z)
release value to zero (0) and increase the Major (X) release value by one.

### B) Update pyproject.toml

1. Edit `pyproject.toml` at the root of this repository and set the
`version =` field to the value determined above.
1. Edit the `authors = ` field to include the name and email of any new
contributors.
1. Double-check that any new dependencies are accurately specified.

### C) Create Release Notes

Edit the `RELEASENOTES.md` file at the root of this repository and add a new
section for the release. The use of an AI assistant for this step can be very
helpful. A simple example prompt to such a tool might be:

```
Generate release notes for the git commits from after the tag 2.1.0 until the
current HEAD and add them as a new entry at the top of `RELEASENOTES.md`.
```

If using an AI assistant, make sure to review the output thoroughly for
accuracy.

### D) Submit a merge request for the release

Commit the local changes to `pyproject.yaml` and `RELEASENOTES.md`, push them
to your personal git fork and submit them as a merge request to the upstream
repository on GitHub.

### E) Create a GitHub release

1. Browse to the
[Releases](https://github.com/fedora-eln/elnbuildsync/releases) page on GitHub
and click on the "Draft a new release" button at the top.
1. On the "Select tag" drop-down menu, choose "Create new tag" and provide the
semantic version (X.Y.Z) as the tag.
1. On the "Previous tag" drop-down menu, select the prior release version that
this release is replacing.
1. In the description field, copy and paste the section from `RELEASENOTES.md`
you created earlier.
1. Click on "Publish release"


## Step 2: Staging Deployment

### A) Update the Ansible role

If the dynamic configuration needs to be updated, make appropriate changes to
the `staging` branch of the
[ELNBuildSync Dynamic Configuration Repository](https://github.com/fedora-eln/elnbuildsync-config/tree/staging).

If the static configuration needs to be updated, make appropriate changes to
[the static configuration template](https://forge.fedoraproject.org/infra/ansible/src/branch/main/roles/openshift-apps/elnbuildsync/templates/elnbuildsync_static.yaml.j2).
This may also involve changes to the
[common](https://forge.fedoraproject.org/infra/ansible/src/branch/main/roles/openshift-apps/elnbuildsync/vars/main.yml)
and/or
[staging](https://forge.fedoraproject.org/infra/ansible/src/branch/main/roles/openshift-apps/elnbuildsync/vars/staging.yml)
variable files.

Once that is complete, the
[staging](https://forge.fedoraproject.org/infra/ansible/src/branch/main/roles/openshift-apps/elnbuildsync/vars/staging.yml)
variable file must be updated to set the `ebs_upstream_ref` value to match the
appropriate release (this can be an upgrade OR a downgrade, if needed).

### B) Create a merge request against the Ansible repository

Commit the changes locally. Make sure to pull and rebase your changes atop the
`main` branch of the
[Infrastructure Ansible Repository](https://forge.fedoraproject.org/infra/ansible/).
Then push to a branch on your personal fork of that repository. Open a merge
request from that branch targeting the `main` branch of the upstream
repository.

All changes to Fedora Infrastructure Ansible need to be reviewed by the Fedora
administrators. They are generally very responsive and will accept or reject
changes fairly quickly, but in the event that an update is urgent, contact
them directly in the
[Fedora Infrastructure Matrix channel](https://matrix.to/#/#admin:fedoraproject.org).

### C) Run the deployment playbook

You must be a member of the
[sysadmin-eln](https://accounts.fedoraproject.org/group/sysadmin-eln/)
group in the Fedora **production** environment to run the playbook.

1. SSH into
[batcave01]((https://docs.fedoraproject.org/en-US/infra/sysadmin_sops/sshaccess/)),
the Ansible control node for Fedora Infrastruture.
1. Run the following command to execute the playbook:
```
sudo rbac-playbook --tags all,build --limit staging openshift-apps/elnbuildsync.yml
```

This will cause the container to be built and then deployed to the staging
environment.

### D) Test the staging deployment

1. Sign into the
[Staging OpenShift Web Console](https://console-openshift-console.apps.ocp.stg.fedoraproject.org/),
locate the pod running the ELNBuildSync service and verify in the logs that
the new version was started.
2. Perform whatever tests are appropriate to validate that the new deployment
is in good shape.


## Step 3: Production Deployment

Deployment to production is very similar to deployment to staging, but there
are several important changes.

**Never** perform a production deployment without first performing a staging
deployment *and testing it thoroughly*.

### Considerations

Before redeploying in production, examine the current state to determine the
optimum timing. For example:

* **Is there a currently-running batch that has been running for an extended
period of time?** It may be worthwhile to enable the "pause" feature and let the
current batch conclude before deploying a new batch, rather than forcing a
long-running batch to start over.

* **Is Fedora Infrastructure currently frozen?** If Fedora Infrastructure is
presently in a Freeze (such as shortly before a Fedora Beta or GA release),
changes to infrastructure deployments, including ELN, require a Freeze-Break
Request ticket be filed in the
[Fedora Infrastructure Tickets](https://forge.fedoraproject.org/infra/tickets/issues)
repository.

### A) Update the Ansible role

If the dynamic configuration needs to be updated, make appropriate changes to
the `production` branch of the
[ELNBuildSync Dynamic Configuration Repository](https://github.com/fedora-eln/elnbuildsync-config/tree/production).

If the static configuration needs to be updated, make appropriate changes to
[the static configuration template](https://forge.fedoraproject.org/infra/ansible/src/branch/main/roles/openshift-apps/elnbuildsync/templates/elnbuildsync_static.yaml.j2).
This may also involve changes to the
[common](https://forge.fedoraproject.org/infra/ansible/src/branch/main/roles/openshift-apps/elnbuildsync/vars/main.yml)
and/or
[production](https://forge.fedoraproject.org/infra/ansible/src/branch/main/roles/openshift-apps/elnbuildsync/vars/production.yml)
variable files.

Once that is complete, the
[production](https://forge.fedoraproject.org/infra/ansible/src/branch/main/roles/openshift-apps/elnbuildsync/vars/production.yml)
variable file must be updated to set the `ebs_upstream_ref` value to match the
appropriate release (this can be an upgrade OR a downgrade, if needed).

### B) Create a merge request against the Ansible repository

Commit the changes locally. Make sure to pull and rebase your changes atop the
`main` branch of the
[Infrastructure Ansible Repository](https://forge.fedoraproject.org/infra/ansible/).
Then push to a branch on your personal fork of that repository. Open a merge
request from that branch targeting the `main` branch of the upstream
repository.

All changes to Fedora Infrastructure Ansible need to be reviewed by the Fedora
administrators. They are generally very responsive and will accept or reject
changes fairly quickly, but in the event that an update is urgent, contact
them directly in the
[Fedora Infrastructure Matrix channel](https://matrix.to/#/#admin:fedoraproject.org).

### C) Run the deployment playbook

You must be a member of the
[sysadmin-eln](https://accounts.fedoraproject.org/group/sysadmin-eln/)
group in the Fedora **production** environment to run the playbook.

1. SSH into
[batcave01]((https://docs.fedoraproject.org/en-US/infra/sysadmin_sops/sshaccess/)),
the Ansible control node for Fedora Infrastruture.
1. Run the following command to execute the playbook:
```
sudo rbac-playbook --tags all,build openshift-apps/elnbuildsync.yml
```

This will cause the container to be built and then deployed to the production
environment.


## Appendices

### Important Fedora Infrastructure Locations
#### Staging
* [Staging ELNBuildSync Status Page](https://elnbuildsync.stg.fedoraproject.org/status.html)
* [Staging ELNBuildSync Status JSON](https://elnbuildsync.stg.fedoraproject.org/status.json)
  * Raw JSON data for the Status Page. May be useful for other dashboards.
* [Staging ELNBuildSync Build Trigger Page](https://elnbuildsync.stg.fedoraproject.org/trigger)
  * Browse here to retrieve an authorization token for `curl`.
* [Staging OpenShift](https://console-openshift-console.apps.ocp.stg.fedoraproject.org/)

#### Production
* [Production ELNBuildSync Status Page](https://elnbuildsync.fedoraproject.org/status.html)
* [Production ELNBuildSync Status JSON](https://elnbuildsync.fedoraproject.org/status.json)
  * Raw JSON data for the Status Page. May be useful for other dashboards.
* [Production ELNBuildSync Build Trigger Page](https://elnbuildsync.fedoraproject.org/trigger)
  * Browse here to retrieve an authorization token for `curl`.
* [Production OpenShift](https://console-openshift-console.apps.ocp.fedoraproject.org/)

#### Infrastructure Ansible Repository
* [Repository Root](https://forge.fedoraproject.org/infra/ansible/)
* [ELNBuildSync Deployment Role](https://forge.fedoraproject.org/infra/ansible/src/branch/main/roles/openshift-apps/elnbuildsync)
* [ELNBuildSync Deployment Playbook](https://forge.fedoraproject.org/infra/ansible/src/branch/main/playbooks/openshift-apps/elnbuildsync.yml)
