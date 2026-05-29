"""Resolve aerender render-setting overrides from queue job fields."""

import json

import os



from app_paths import config_path



DEFAULT_PROXY_RS = "Best Settings"

USE_QUEUE_RS = "(Use queue)"

DEFAULT_RS_TEMPLATES = [

    USE_QUEUE_RS,

    "Best Settings",

    "Draft Settings",

    "Multi-Machine Settings",

]





def job_skip_enabled(job):

    return job.get("skip_existing") in (True, "1", 1) or job.get("skip_val") in (

        "1",

        1,

        True,

    )





def _load_config():

    cfg_file = config_path()

    if os.path.isfile(cfg_file):

        try:

            with open(cfg_file, encoding="utf-8") as handle:

                return json.load(handle)

        except (json.JSONDecodeError, OSError):

            pass

    return {}





def get_proxy_rs_template():

    cfg = _load_config()

    return (cfg.get("proxy_rs_template") or DEFAULT_PROXY_RS).strip()





def get_rs_template_options(extra=None):

    cfg = _load_config()

    templates = cfg.get("rs_templates")

    if isinstance(templates, list) and templates:

        options = [str(t).strip() for t in templates if str(t).strip()]

    else:

        options = list(DEFAULT_RS_TEMPLATES)

    if USE_QUEUE_RS not in options:

        options.insert(0, USE_QUEUE_RS)

    for name in extra or []:

        name = str(name).strip()

        if name and name not in options:

            options.append(name)

    return options





def is_use_queue_rs(name):

    text = (name or "").strip()

    return not text or text in (USE_QUEUE_RS, "(default)", "—", "-")





def resolve_rs_template(job):

    """

    Return -RStemplate value or empty string to use render-queue settings from the .aep.

    "(Use queue)" keeps skip existing, proxies, and OM paths from the scanned RQ item.

    """

    if job_skip_enabled(job):

        return ""

    rs = (job.get("rs_template") or "").strip()

    if rs and not is_use_queue_rs(rs):

        return rs

    use_proxy = job.get("use_proxy") in (True, "1", 1)

    if use_proxy:

        return get_proxy_rs_template()

    return ""





def resolve_frame_value(value):

    if value is None:

        return None

    text = str(value).strip()

    if not text:

        return None

    return int(text)





def frame_or_default(value, default):

    """Parse frame number; never use `or` — frame 0 is valid."""

    parsed = resolve_frame_value(value)

    if parsed is not None:

        return parsed

    return default


def resolve_frame_range(start, end):
    """Return (start, end) from queue values, or (None, None) if unset."""
    return resolve_frame_value(start), resolve_frame_value(end)

