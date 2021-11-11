import re

from . import config
from . import kojihelpers
from . import listener
from . import rebuild_data

from twisted.internet import reactor
from twisted.internet.defer import AlreadyCalledError, inlineCallbacks

logger = config.logger


# Matching the namespace/component text format
cre = re.compile(r"^(?P<namespace>rpms|modules)/(?P<component>[A-Za-z0-9:._+-]+)$")


@inlineCallbacks
def run(compset):
    """Processes the supplied set of components.  If the set is empty,
    fetch all latest components from the trigger tags.

    :param compset: A set of components to process in the `ns/comp` form
    :returns: None
    """
    if not config.main:
        logger.critical("DistroBuildSync is not configured, aborting.")
        return None

    if not compset:
        logger.debug("No components selected, gathering components from triggers.")
        compset.update(
            "{}/{}".format("rpms", x["package_name"])
            for x in kojihelpers.get_buildsys("source").listTagged(
                config.main["trigger"]["rpms"], latest=True
            )
        )

    logger.info("Processing %d component(s).", len(compset))
    rd_list = []
    for rec in sorted(compset, key=str.lower):
        m = cre.match(rec)
        if m is None:
            logger.error("Cannot process %s; looks like garbage.", rec)
            continue

        m = m.groupdict()
        try:
            rd_list.append(rebuild_data.rebuild_data_from_component(m["namespace"], m["component"]))
        except ValueError as e:
            logger.info(e)
            continue

        logger.debug("Scheduled {}/{} for rebuild".format(m["namespace"], m["component"]))

    # Fire off the builds
    yield listener.rebuild_batch(config.main["build"]["target"], rd_list)

    rd_list_len = len(rd_list)
    skipped = len(compset) - rd_list_len
    logger.info(f"Synchronized {rd_list_len} component(s), {skipped} skipped.")

    # End the program
    reactor.stop()


def process_message(msg):
    # Listen for repositories we are waiting on.
    if msg.topic.endswith("buildsys.repo.done"):
        tag = msg.body["tag"]
        if tag in config.awaited_repos:
            logger.info(f"Repo {tag} has regenerated")
            for deferred in config.awaited_repos[tag]:
                try:
                    deferred.callback(None)
                except AlreadyCalledError:
                    # Most likely due to a timeout, so ignore it
                    pass
            # Clear the awaited list
            del config.awaited_repos[tag]
