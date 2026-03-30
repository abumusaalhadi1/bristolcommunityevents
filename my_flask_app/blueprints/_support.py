from __future__ import annotations

import sys


def get_app_module():
    main_module = sys.modules.get("__main__")
    if main_module is not None and getattr(main_module, "app", None) is not None:
        return main_module

    import app as app_module

    return app_module


def register_url_rules(state, routes):
    app_module = get_app_module()
    for rule, function_name, options in routes:
        route_options = dict(options or {})
        endpoint = route_options.pop("endpoint", function_name)
        view_func = getattr(app_module, function_name)
        state.app.add_url_rule(rule, endpoint=endpoint, view_func=view_func, **route_options)


def register_error_handlers(state, handlers):
    app_module = get_app_module()
    for code, function_name in handlers:
        state.app.register_error_handler(code, getattr(app_module, function_name))

