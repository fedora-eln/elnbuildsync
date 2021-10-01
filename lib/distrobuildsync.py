import logging
import os
import tempfile
import datetime
import requests

import git
import koji
import yaml
import re
import json

# Global logger
logger = logging.getLogger(__name__)

# Global configuration config
c = dict()

# Retry attempts if things fail
retry = 3

# Running in the dry run mode
dry_run = False

distrogitsync = None

# Matching the namespace/component text format
cre = re.compile(r"^(?P<namespace>rpms|modules)/(?P<component>[A-Za-z0-9:._+-]+)$")


def loglevel(val=None):
    """Gets or, optionally, sets the logging level of the module.
    Standard numeric levels are accepted.

    :param val: The logging level to use, optional
    :returns: The current logging level
    """
    if val is not None:
        try:
            logger.setLevel(val)
        except ValueError:
            logger.warning(
                "Invalid log level passed to DistroBuildSync logger: %s", val
            )
        except Exception:
            logger.exception("Unable to set log level: %s", val)
    return logger.getEffectiveLevel()


def retries(val=None):
    """Gets or, optionally, sets the number of retries for various
    operational failures.  Typically used for handling dist-git requests.

    :param val: The number of retries to attept, optional
    :returns: The current value of retries
    """
    global retry
    if val is not None:
        retry = val
    return retry


def pretend(val=None):
    """Gets and, optionally, sets the dry_run mode.

    :param val: True to run in dry_run, False otherwise, optional
    :returns: The current value of the dry_run mode
    """
    global dry_run
    if val is not None:
        dry_run = val
    return dry_run


def distrogitsync_url(val=None):
    """Gets and, optionally, sets the distrogitsync_url mode."""
    global distrogitsync
    if val is not None:
        distrogitsync = val
    return distrogitsync


def get_config():
    """Gets the current global configuration dictionary.

    The dictionary may be empty if no configuration has been successfully
    loaded yet.

    :returns: The global configuration dictionary
    """
    return c


def split_scmurl(scmurl):
    """Splits a `link#ref` style URLs into the link and ref parts.  While
    generic, many code paths in DistroBuildSync expect these to be branch names.
    `link` forms are also accepted, in which case the returned `ref` is None.

    It also attempts to extract the namespace and component, where applicable.
    These can only be detected if the link matches the standard dist-git
    pattern; in other cases the results may be bogus or None.

    :param scmurl: A link#ref style URL, with #ref being optional
    :returns: A dictionary with `link`, `ref`, `ns` and `comp` keys
    """
    scm = scmurl.split("#", 1)
    nscomp = scm[0].split("/")
    return {
        "link": scm[0],
        "ref": scm[1] if len(scm) >= 2 else None,
        "ns": nscomp[-2] if len(nscomp) >= 2 else None,
        "comp": nscomp[-1] if nscomp else None,
    }


def split_module(comp):
    """Splits modules component name into name and stream pair.  Expects the
    name to be in the `name:stream` format.  Defaults to stream=master if the
    split fails.

    :param comp: The component name
    :returns: Dictionary with name and stream
    """
    ms = comp.split(":")
    return {
        "name": ms[0],
        "stream": ms[1] if len(ms) > 1 and ms[1] else "master",
    }


# FIXME: This needs even more error checking, e.g.
#         - check if blocks are actual dictionaries
#         - check if certain values are what we expect
def load_config(crepo):
    """Loads or updates the global configuration from the provided URL in
    the `link#branch` format.  If no branch is provided, assumes `master`.

    The operation is atomic and the function can be safely called to update
    the configuration without the danger of clobbering the current one.

    `crepo` must be a git repository with `distrobaker.yaml` in it.

    :param crepo: `link#branch` style URL pointing to the configuration
    :returns: The configuration dictionary, or None on error
    """
    global c
    cdir = tempfile.TemporaryDirectory(prefix="distrobaker-")
    logger.info("Fetching configuration from %s to %s", crepo, cdir.name)
    scm = split_scmurl(crepo)
    if scm["ref"] is None:
        scm["ref"] = "master"
    for attempt in range(retry):
        try:
            git.Repo.clone_from(scm["link"], cdir.name).git.checkout(scm["ref"])
        except Exception:
            logger.warning(
                "Failed to fetch configuration, retrying (#%d).",
                attempt + 1,
                exc_info=True,
            )
            continue
        else:
            logger.info("Configuration fetched successfully.")
            break
    else:
        logger.error("Failed to fetch configuration, giving up.")
        return None
    if os.path.isfile(os.path.join(cdir.name, "distrobaker.yaml")):
        try:
            with open(os.path.join(cdir.name, "distrobaker.yaml")) as f:
                y = yaml.safe_load(f)
            logger.debug(
                "%s loaded, processing.",
                os.path.join(cdir.name, "distrobaker.yaml"),
            )
        except Exception:
            logger.exception("Could not parse distrobaker.yaml.")
            return None
    else:
        logger.error("Configuration repository does not contain distrobaker.yaml.")
        return None
    n = dict()
    if "configuration" in y:
        cnf = y["configuration"]
        for k in ("source", "destination"):
            if k in cnf:
                n[k] = dict()
                if "scm" in cnf[k]:
                    n[k]["scm"] = str(cnf[k]["scm"])
                else:
                    logger.error("Configuration error: %s.scm missing.", k)
                    return None
                if "cache" in cnf[k]:
                    n[k]["cache"] = dict()
                    for kc in ("url", "cgi", "path"):
                        if kc in cnf[k]["cache"]:
                            n[k]["cache"][kc] = str(cnf[k]["cache"][kc])
                        else:
                            logger.error(
                                "Configuration error: %s.cache.%s missing.",
                                k,
                                kc,
                            )
                            return None
                else:
                    logger.error("Configuration error: %s.cache missing.", k)
                    return None
                if "profile" in cnf[k]:
                    n[k]["profile"] = str(cnf[k]["profile"])
                else:
                    logger.error("Configuration error: %s.profile missing.", k)
                    return None
                if "mbs" in cnf[k]:
                    n[k]["mbs"] = cnf[k]["mbs"]
                else:
                    logger.error("Configuration error: %s.mbs missing.", k)
                    return None
            else:
                logger.error("Configuration error: %s missing.", k)
                return None
        if "trigger" in cnf:
            n["trigger"] = dict()
            for k in ("rpms", "modules"):
                if k in cnf["trigger"]:
                    n["trigger"][k] = str(cnf["trigger"][k])
                else:
                    logger.error("Configuration error: trigger.%s missing.", k)
        else:
            logger.error("Configuration error: trigger missing.")
            return None
        if "build" in cnf:
            n["build"] = dict()
            for k in ("prefix", "target", "platform"):
                if k in cnf["build"]:
                    n["build"][k] = str(cnf["build"][k])
                else:
                    logger.error("Configuration error: build.%s missing.", k)
                    return None
            if "scratch" in cnf["build"]:
                n["build"]["scratch"] = bool(cnf["build"]["scratch"])
            else:
                logger.warning(
                    "Configuration warning: build.scratch not defined, assuming false."
                )
                n["build"]["scratch"] = False
        else:
            logger.error("Configuration error: build missing.")
            return None
        if "git" in cnf:
            n["git"] = dict()
            for k in ("author", "email", "message"):
                if k in cnf["git"]:
                    n["git"][k] = str(cnf["git"][k])
                else:
                    logger.error("Configuration error: git.%s missing.", k)
                    return None
        else:
            logger.error("Configuration error: git missing.")
            return None
        if "control" in cnf:
            n["control"] = dict()
            for k in ("build", "merge", "strict"):
                if k in cnf["control"]:
                    n["control"][k] = bool(cnf["control"][k])
                else:
                    logger.error("Configuration error: control.%s missing.", k)
                    return None
            n["control"]["exclude"] = {"rpms": set(), "modules": set()}
            if "exclude" in cnf["control"]:
                for cns in ("rpms", "modules"):
                    if cns in cnf["control"]["exclude"]:
                        n["control"]["exclude"][cns].update(
                            cnf["control"]["exclude"][cns]
                        )
            for cns in ("rpms", "modules"):
                if n["control"]["exclude"]["rpms"]:
                    logger.info(
                        "Excluding %d component(s) from the %s namespace.",
                        len(n["control"]["exclude"][cns]),
                        cns,
                    )
                else:
                    logger.info(
                        "Not excluding any components from the %s namespace.",
                        cns,
                    )
        else:
            logger.error("Configuration error: control missing.")
            return None
        if "defaults" in cnf:
            n["defaults"] = dict()
            for dk in ("cache", "rpms", "modules"):
                if dk in cnf["defaults"]:
                    n["defaults"][dk] = dict()
                    for dkk in ("source", "destination"):
                        if dkk in cnf["defaults"][dk]:
                            n["defaults"][dk][dkk] = str(cnf["defaults"][dk][dkk])
                        else:
                            logger.error(
                                "Configuration error: defaults.%s.%s missing.",
                                dk,
                                dkk,
                            )
                else:
                    logger.error("Configuration error: defaults.%s missing.", dk)
                    return None
        else:
            logger.error("Configuration error: defaults missing.")
            return None
    else:
        logger.error("The required configuration block is missing.")
        return None
    components = 0
    nc = {
        "rpms": dict(),
        "modules": dict(),
    }
    if "components" in y:
        cnf = y["components"]
        for k in ("rpms", "modules"):
            if k in cnf:
                for p in cnf[k].keys():
                    components += 1
                    nc[k][p] = dict()
                    cname = p
                    sname = ""
                    if k == "modules":
                        ms = split_module(p)
                        cname = ms["name"]
                        sname = ms["stream"]
                    nc[k][p]["source"] = n["defaults"][k]["source"] % {
                        "component": cname,
                        "stream": sname,
                    }
                    nc[k][p]["destination"] = n["defaults"][k]["destination"] % {
                        "component": cname,
                        "stream": sname,
                    }
                    nc[k][p]["cache"] = {
                        "source": n["defaults"]["cache"]["source"]
                        % {"component": cname, "stream": sname},
                        "destination": n["defaults"]["cache"]["destination"]
                        % {"component": cname, "stream": sname},
                    }
                    if cnf[k][p] is None:
                        cnf[k][p] = dict()
                    for ck in ("source", "destination"):
                        if ck in cnf[k][p]:
                            nc[k][p][ck] = str(cnf[k][p][ck])
                    if "cache" in cnf[k][p]:
                        for ck in ("source", "destination"):
                            if ck in cnf[k][p]["cache"]:
                                nc[k][p]["cache"][ck] = str(cnf[k][p]["cache"][ck])
            logger.info(
                "Found %d configured component(s) in the %s namespace.",
                len(nc[k]),
                k,
            )
    if n["control"]["strict"]:
        logger.info(
            "Running in the strict mode.  Only configured components will be processed."
        )
    else:
        logger.info(
            "Running in the non-strict mode.  All trigger components will be processed."
        )
    if not components:
        if n["control"]["strict"]:
            logger.warning(
                "No components configured while running in the strict mode.  Nothing to do."
            )
        else:
            logger.info("No components explicitly configured.")
    c["main"] = n
    c["comps"] = nc
    return c


def build_comp(comp, ref, ns="rpms", target=None, ref_overrides=None):
    """Submits a build for the requested component.  Requires the
    component name, namespace and the destination SCM reference to build.
    The build is submitted for the configured build target.  The build
    SCMURL is prefixed with the configured prefix.

    In the dry-run mode, the returned task ID is 0.

    :param comp: The component name
    :param ref: The SCM reference
    :param ns: The component namespace
    :param target: Koji target, if None, `c["main"]["build"]["target"]` is used.
    :returns: The build system task ID for RPMS, the module build ID for
        modules, or None on error
    """
    if "main" not in c:
        logger.critical("DistroBuildSync is not configured, aborting.")
        return None
    if comp in c["main"]["control"]["exclude"][ns]:
        logger.critical(
            "The component %s/%s is excluded from sync, aborting.", ns, comp
        )
        return None
    logger.info("Processing build for %s/%s.", ns, comp)
    buildcomp = comp
    if comp in c["comps"][ns]:
        buildcomp = split_scmurl(c["comps"][ns][comp]["destination"])["comp"]
    if ns == "rpms":
        bsys = get_buildsys("destination")
        try:
            if not dry_run:
                task = bsys.build(
                    "{}/{}/{}#{}".format(
                        c["main"]["build"]["prefix"], ns, buildcomp, ref
                    ),
                    target or c["main"]["build"]["target"],
                    {"scratch": c["main"]["build"]["scratch"]},
                )
                logger.debug(
                    "Build submitted for %s/%s; task %d; SCMURL: %s/%s/%s#%s.",
                    ns,
                    comp,
                    task,
                    c["main"]["build"]["prefix"],
                    ns,
                    buildcomp,
                    ref,
                )
            else:
                task = 0
                logger.info(
                    "Running in the dry mode, not submitting any builds for %s/%s (%s/%s/%s#%s), target %s.",
                    ns,
                    comp,
                    c["main"]["build"]["prefix"],
                    ns,
                    buildcomp,
                    ref,
                    target or c["main"]["build"]["target"],
                )
            return task
        except Exception:
            logger.exception(
                "Failed submitting build for %s/%s (%s/%s/%s#%s).",
                ns,
                comp,
                c["main"]["build"]["prefix"],
                ns,
                comp,
                ref,
            )
            return None
    elif ns == "modules":
        ms = split_module(buildcomp)
        buildscmurl = "{}/{}/{}.git?#{}".format(
            c["main"]["build"]["prefix"], ns, ms["name"], ref
        )
        ps = split_module(c["main"]["build"]["platform"])
        cdst = c["main"]["defaults"][ns]["destination"] % {
            "component": ms["name"],
            "stream": ms["stream"],
        }
        branch = split_scmurl(cdst)["ref"]
        body = {
            "scmurl": buildscmurl,
            "branch": branch,
            "buildrequire_overrides": {ps["name"]: [ps["stream"]]},
            "scratch": c["main"]["build"]["scratch"],
            "rpm_component_ref_overrides": ref_overrides,
        }
        request_url = "{}/{}/".format(
            c["main"]["destination"]["mbs"]["api_url"], "module-builds"
        )
        logger.info(
            "Body of build request for %s/%s to POST to %s using auth_method %s: %s",
            ns,
            comp,
            request_url,
            c["main"]["destination"]["mbs"]["auth_method"],
            body,
        )

        if not dry_run:
            if c["main"]["destination"]["mbs"]["auth_method"] == "kerberos":
                try:
                    import requests_kerberos

                    data = json.dumps(body)
                    auth = requests_kerberos.HTTPKerberosAuth(
                        mutual_authentication=requests_kerberos.OPTIONAL,
                    )
                    resp = requests.post(request_url, data=data, auth=auth)
                except Exception:
                    logger.exception(
                        "Failed submitting build for %s/%s (%s).",
                        ns,
                        comp,
                        buildscmurl,
                    )
                    return None

            elif c["main"]["destination"]["mbs"]["auth_method"] == "oidc":
                try:
                    import openidc_client

                    mapping = {
                        "Token": "Token",
                        "Authorization": "Authorization",
                    }
                    # Get the auth token using the OpenID client
                    oidc = openidc_client.OpenIDCClient(
                        "mbs_build",
                        c["main"]["destination"]["mbs"]["oidc_id_provider"],
                        mapping,
                        c["main"]["destination"]["mbs"]["oidc_client_id"],
                        c["main"]["destination"]["mbs"]["oidc_client_secret"],
                    )
                    resp = oidc.send_request(
                        request_url,
                        http_method="POST",
                        json=body,
                        scopes=c["main"]["destination"]["mbs"]["oidc_scopes"],
                    )
                except Exception:
                    logger.exception(
                        "Failed submitting build for %s/%s (%s).",
                        ns,
                        comp,
                        buildscmurl,
                    )
                    return None
            else:
                logger.critical(
                    "Cannot build %s/%s; unknown auth_method: %s",
                    ns,
                    comp,
                    c["main"]["destination"]["mbs"]["auth_method"],
                )
                return None

            logger.debug(
                "Build request for %s/%s (%s) returned status %d.",
                ns,
                comp,
                buildscmurl,
                resp.status_code,
            )
            if resp.status_code == 401:
                logger.critical(
                    "Cannot build %s/%s: MBS authentication failed using auth_method %s. "
                    "Make sure you have a valid ticket/token.",
                    ns,
                    comp,
                    c["main"]["destination"]["mbs"]["auth_method"],
                )
                return None
            elif not resp.ok:
                logger.critical(
                    "Cannot build %s/%s: request failed with: %s",
                    ns,
                    comp,
                    resp.text,
                )
                return None

            rdata = resp.json()
            build = rdata[0] if isinstance(rdata, list) else rdata
            buildid = build["id"]
            logger.debug(
                "Build submitted for %s/%s; buildid %d; SCMURL: %s.",
                ns,
                comp,
                buildid,
                buildscmurl,
            )
            return buildid

        else:
            logger.info(
                "Running in the dry mode, not submitting any builds for %s/%s (%s).",
                ns,
                comp,
                buildscmurl,
            )
            return 0
    else:
        logger.critical("Cannot build %s/%s; unknown namespace.", ns, comp)
        return None


def create_side_tag(downstream_target, upstream_sidetag):
    """
    Creates new downstream sidetag based inheriting the build tag of
    `downstream_target` and adds `downstream_sidetag` "extra" record
    to `upstream_sidetag` in upstream Koji so it is possible to map
    upstream sidetag to downstream sidetag.

    If the `downstream_sidetag` already exists, it returns it.

    :params str downstream_target: Downstream target name.
    :params str upstream_sidetag: Upstream sidetag name.
    :return str: Name of the downstream sidetag.
    """

    # Create downstream sidetag only if it does not exist.
    upstream_koji = get_buildsys("source", force_login=True)
    upstream_tag = upstream_koji.getTag(upstream_sidetag)
    if "downstream_sidetag" in upstream_tag["extra"]:
        downstream_sidetag = upstream_tag["extra"]["downstream_sidetag"]
        logger.info(
            "Downstream sidetag for %s already exists: %s."
            % (upstream_sidetag, downstream_sidetag)
        )
        return downstream_sidetag

    logger.info("Creating downstream sidetag for %s." % upstream_sidetag)
    # Get downstream build tag.
    downstream_koji = get_buildsys("destination")
    downstream_target = downstream_koji.getBuildTarget(downstream_target)
    downstream_tag = downstream_target["build_tag_name"]

    # Create downstream sidetag
    if not dry_run:
        downstream_sidetag = downstream_koji.createSideTag(
            downstream_tag, suffix="stack-gate"
        )["name"]
    else:
        logger.info(
            "Running in dry_run mode, not creating downstream_sidetag for %s."
            % downstream_tag
        )
        downstream_sidetag = "%s-dry-run-mode-stack-gate" % downstream_tag

    # Set the mapping between upstream sidetag and downstream sidetag.
    if not dry_run:
        upstream_koji.editTag2(
            upstream_sidetag, extra={"downstream_sidetag": downstream_sidetag}
        )
        logger.info(
            "Downstream sidetag for %s created: %s."
            % (upstream_sidetag, downstream_sidetag)
        )
    else:
        logger.info(
            "Running in dry_run mode, not editing upstream_sidetag %s ."
            % upstream_sidetag
        )
    return downstream_sidetag


def call_distrogitsync(ns, comp, ref_overrides=None):
    compset = [(ns, comp)]
    ref_overrides = ref_overrides or {}
    for c in ref_overrides.keys():
        compset.append(("rpms", c))
    for namespace, c in compset:
        if distrogitsync:
            logger.info("Calling distrogitsync for %s/%s" % (namespace, c))
            try:
                r = requests.post("%s/%s/%s" % (distrogitsync, namespace, c))
                r.raise_for_status()
            except requests.exceptions.RequestException:
                logger.exception("Failed to contact distrogitsync")
                continue


def rebuild_downstream(ns, comp, nvr, downstream_target=None, ref_overrides=None):
    """
    Rebuilds the component `comp` with NVR `nvr` in `downstream_target`.

    :param str comp: Name of the component. For example "firefox".
    :param str NVR: NVR of the component. For example "firefox-1-1".
    :param str downstream_target: Name of the downstream target to build the component
        in or None to use the default one from configuration file.
    """
    if comp in c["comps"][ns] or not c["main"]["control"]["strict"]:
        if comp in c["main"]["control"]["exclude"][ns]:
            logger.info(
                "The %s/%s component is excluded from sync, skipping.",
                ns,
                comp,
            )
            return

        build_info = get_build_info(nvr)
        comp_name = comp
        if ns == "modules":
            comp = "%s:%s" % (build_info["name"], build_info["stream"])

        scmurl = build_info["scmurl"]
        ref = split_scmurl(scmurl)["ref"]
        if ref is not None:
            call_distrogitsync(ns, comp_name, ref_overrides)
            task = build_comp(
                comp, ref, ns=ns, target=downstream_target, ref_overrides=ref_overrides
            )
            if task is not None:
                logger.info(
                    "Build submission of %s/%s complete, task %s, trigger processed.",
                    ns,
                    comp,
                    task,
                )
            else:
                logger.error(
                    "Build submission of %s/%s failed, aborting.trigger.",
                    ns,
                    comp,
                )
        else:
            logger.error(
                "Synchronization of %s/%s failed, aborting trigger.",
                ns,
                comp,
            )
    else:
        logger.debug(
            "Component %s not configured for sync and the strict mode is enabled, ignoring.",
            comp,
        )


def get_ref_overrides(modulemd):
    """
    Get RPM components ref overrides from the modulemd file.
    """
    ref_overrides = {}
    data = yaml.safe_load(modulemd)
    for name, rpm_data in data["data"]["xmd"]["mbs"]["rpms"].items():
        ref_overrides[name] = rpm_data["ref"]
    logger.info("RPM ref overrides %r:" % (ref_overrides))
    return ref_overrides


def process_message(msg):
    """Processes a fedora-messaging messages.  We can only handle Koji
    tagging events; messaging should be configured properly.

    If the message is recognized and matches our configuration or mode,
    the function calls `build_comp()`.

    :param msg: fedora-messaging message
    :returns: None
    """
    if "main" not in c:
        logger.critical("DistroBuildSync is not configured, aborting.")
        return None
    logger.debug("Received a message with topic %s.", msg.topic)
    if msg.topic.endswith("buildsys.tag"):
        try:
            logger.debug("Processing a tagging event message.")
            comp = msg.body["name"]
            nvr = "{}-{}-{}".format(
                msg.body["name"], msg.body["version"], msg.body["release"]
            )
            tag = msg.body["tag"]
            logger.debug("Tagging event for %s, tag %s received.", comp, tag)
        except Exception:
            logger.exception("Failed to process the message: %s", msg)
            return None
        upstream_build_tag = c["main"]["trigger"]["rpms"].replace("-gate", "-build")
        if tag == c["main"]["trigger"]["rpms"]:
            logger.info("Handling an RPM trigger for %s, tag %s.", comp, tag)
            rebuild_downstream("rpms", comp, nvr)
        elif tag == c["main"]["trigger"]["modules"]:
            logger.info("Handling an Module trigger for %s, tag %s.", comp, tag)
            bi = get_build_info(nvr)
            ref_overrides = get_ref_overrides(bi["modulemd"])
            rebuild_downstream("modules", comp, nvr, ref_overrides=ref_overrides)
        elif (
            tag.startswith(upstream_build_tag) and tag.endswith("-stack-gate")
        ) or tag.startswith("%s-side" % upstream_build_tag):
            logger.info("Handling a sidetag RPM trigger for %s, tag %s.", comp, tag)
            downstream_sidetag = create_side_tag(c["main"]["build"]["target"], tag)
            rebuild_downstream("rpms", comp, nvr, downstream_sidetag)
        else:
            logger.debug("Message tag not configured as a trigger, ignoring.")
    else:
        logger.warning("Unable to handle %s topics, ignoring.", msg.topic)
    return None


def process_components(compset):
    """Processes the supplied set of components.  If the set is empty,
    fetch all latest components from the trigger tags.

    :param compset: A set of components to process in the `ns/comp` form
    :returns: None
    """
    if "main" not in c:
        logger.critical("DistroBuildSync is not configured, aborting.")
        return None

    if not compset:
        logger.debug("No components selected, gathering components from triggers.")
        compset.update(
            "{}/{}".format("rpms", x["package_name"])
            for x in get_buildsys("source").listTagged(
                c["main"]["trigger"]["rpms"], latest=True
            )
        )
    logger.info("Processing %d component(s).", len(compset))
    processed = 0
    for rec in sorted(compset, key=str.lower):
        m = cre.match(rec)
        if m is None:
            logger.error("Cannot process %s; looks like garbage.", rec)
            continue
        m = m.groupdict()
        logger.info("Processing %s.", rec)
        if m["component"] in c["main"]["control"]["exclude"][m["namespace"]]:
            logger.info(
                "The %s/%s component is excluded from sync, skipping.",
                m["namespace"],
                m["component"],
            )
            continue
        if (
            c["main"]["control"]["strict"]
            and m["component"] not in c["comps"][m["namespace"]]
        ):
            logger.info(
                "The %s/%s component not configured while the strict mode is enabled, ignoring.",
                m["namespace"],
                m["component"],
            )
            continue
        nvr = get_build(m["component"], m["namespace"])
        if not nvr:
            logger.info(
                "The %s/%s component's build not tagged in the source Koji tag.",
                m["namespace"],
                m["component"],
            )
            continue
        bi = get_build_info(nvr)
        scmurl = bi["scmurl"]
        ref = split_scmurl(scmurl)["ref"]
        if ref:
            if m["namespace"] == "modules":
                ref_overrides = get_ref_overrides(bi["modulemd"])
            else:
                ref_overrides = None
            call_distrogitsync(m["namespace"], m["component"], ref_overrides)
            build_comp(m["component"], ref, m["namespace"], ref_overrides=ref_overrides)
        else:
            logger.error("No git reference in %s." % scmurl)
        logger.info("Done processing %s.", rec)
        processed += 1
    logger.info(
        "Synchronized %d component(s), %d skipped.",
        processed,
        len(compset) - processed,
    )
    return None


def get_build_info(nvr):
    """Get SCMURL, plus extra attributes for modules, for a source build system
    build NVR.  NVRs are unique.

    :param nvr: The build NVR to look up
    :returns: A dictionary with `scmurl`, `name`, `stream`, and `modulemd` keys,
    or None on error
    """
    if "main" not in c:
        logger.critical("DistroBuildSync is not configured, aborting.")
        return None
    bsys = get_buildsys("source")
    if bsys is None:
        logger.error(
            "Build system unavailable, cannot retrieve the build info of %s.",
            nvr,
        )
        return None
    try:
        bsrc = bsys.getBuild(nvr)
    except Exception:
        logger.exception(
            "An error occured while retrieving the build info for %s.", nvr
        )
        return None

    bi = dict()
    if "source" in bsrc:
        bi["scmurl"] = bsrc["source"]
        logger.debug("Retrieved SCMURL for %s: %s", nvr, bi["scmurl"])
    else:
        logger.error("Cannot find any SCMURL associated with %s.", nvr)
        return None

    try:
        minfo = bsrc["extra"]["typeinfo"]["module"]
        bi["name"] = minfo["name"]
        bi["stream"] = minfo["stream"]
        bi["module_version"] = minfo["version"]
        bi["modulemd"] = minfo["modulemd_str"]
        logger.debug(
            "Actual name:stream for %s is %s:%s", nvr, bi["name"], bi["stream"]
        )
    except Exception:
        bi["name"] = None
        bi["stream"] = None
        bi["module_version"] = None
        bi["modulemd"] = None
        logger.debug("No module info for %s.", nvr)

    return bi


def get_build(comp, ns="rpms"):
    """Get the latest build NVR for the specified component.  Searches the
    component namespace trigger tag to locate this.  Note this is not the
    highest NVR, it's the latest tagged build.

    :param comp: The component name
    :param ns: The component namespace
    :returns: NVR of the latest build, or None on error
    """
    if "main" not in c:
        logger.critical("DistroBuildSync is not configured, aborting.")
        return None
    bsys = get_buildsys("source")
    if bsys is None:
        logger.error(
            "Build system unavailable, cannot find the latest build for %s/%s.",
            ns,
            comp,
        )
        return None
    if ns == "rpms":
        try:
            nvr = bsys.listTagged(c["main"]["trigger"][ns], package=comp, latest=True)
        except Exception:
            logger.exception(
                "An error occured while getting the latest build for %s/%s.",
                ns,
                comp,
            )
            return None
        if nvr:
            logger.debug(
                "Located the latest build for %s/%s: %s",
                ns,
                comp,
                nvr[0]["nvr"],
            )
            return nvr[0]["nvr"]
        logger.error("Did not find any builds for %s/%s.", ns, comp)
        return None

    if ns == "modules":
        ms = split_module(comp)
        cname = ms["name"]
        sname = ms["stream"]
        try:
            builds = bsys.listTagged(
                c["main"]["trigger"][ns],
            )
        except Exception:
            logger.exception(
                "An error occured while getting the latest builds for %s/%s.",
                ns,
                cname,
            )
            return None
        if not builds:
            logger.error("Did not find any builds for %s/%s.", ns, cname)
            return None
        logger.debug(
            "Found %d total builds for %s/%s",
            len(builds),
            ns,
            cname,
        )
        # find the latest build for name:stream
        latest = None
        latest_version = 0
        for b in builds:
            binfo = get_build_info(b["nvr"])
            if binfo is None or binfo["name"] is None or binfo["stream"] is None:
                logger.error(
                    "Could not get module info for %s, skipping.",
                    b["nvr"],
                )
            elif (
                cname == binfo["name"]
                and sname == binfo["stream"]
                and int(binfo["module_version"]) >= latest_version
            ):
                latest = b["nvr"]
                latest_version = int(binfo["module_version"])
        if latest:
            logger.debug("Located the latest build for %s/%s: %s", ns, comp, latest)
            return latest
        logger.error("Did not find any builds for %s/%s.", ns, comp)
        return None

    logger.error("Unrecognized namespace: %s/%s", ns, comp)
    return None


def get_buildsys(which, force_login=False):
    """Get a koji build system session for either the source or the
    destination.  Caches the sessions so future calls are cheap.
    Destination sessions are authenticated, source sessions are not.

    :param which: Session to select, source or destination
    :param bool force_login: Login also on source instance.
    :returns: Koji session object, or None on error
    """
    if "main" not in c:
        logger.critical("DistroBuildSync is not configured, aborting.")
        return None
    if which not in ("source", "destination"):
        logger.error('Cannot get "%s" build system.', which)
        return None

    session_timed_out = False
    if hasattr(get_buildsys, which):
        session_age = datetime.datetime.now() - getattr(
            get_buildsys, which + "_session_start_time"
        )
        # slightly less than an hour, to be safe
        if session_age.seconds > 3550 or session_age.days > 0:
            session_timed_out = True

    if session_timed_out or not hasattr(get_buildsys, which) or force_login:
        logger.debug(
            'Initializing the %s koji instance with the "%s" profile.',
            which,
            c["main"][which]["profile"],
        )
        try:
            bsys = koji.read_config(profile_name=c["main"][which]["profile"])
            bsys = koji.ClientSession(bsys["server"], opts=bsys)
        except Exception:
            logger.exception(
                'Failed initializing the %s koji instance with the "%s" profile, skipping.',
                which,
                c["main"][which]["profile"],
            )
            return None
        logger.debug("The %s koji instance initialized.", which)
        if which == "destination" or force_login:
            logger.debug("Authenticating with the %s koji instance." % which)
            try:
                if session_timed_out:
                    bsys.logout()
                bsys.gssapi_login()
            except Exception:
                logger.exception(
                    "Failed authenticating against the %s koji instance, skipping."
                    % which
                )
                return None
            logger.debug(
                "Successfully authenticated with the %s koji instance." % which
            )
        if which == "source":
            get_buildsys.source = bsys
            get_buildsys.source_session_start_time = datetime.datetime.now()
        else:
            get_buildsys.destination = bsys
            get_buildsys.destination_session_start_time = datetime.datetime.now()
    else:
        logger.debug(
            "The %s koji instance is already initialized, fetching from cache.",
            which,
        )
    return vars(get_buildsys)[which]
