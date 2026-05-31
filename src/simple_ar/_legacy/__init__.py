"""Private transitional modules kept during the reboot refactor.

These files still contain the old CLI and stage-handler orchestration. New code
should live in domain packages such as ``core``, ``research``, ``experiment``,
and ``code_task``. Public compatibility shims remain at ``simple_ar.cli`` and
``simple_ar._legacy.stage_handlers`` while the large modules are retired incrementally.
"""
