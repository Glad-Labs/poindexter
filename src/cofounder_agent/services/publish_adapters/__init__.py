"""Core publish adapters shipped with Poindexter.

Each module in this package exposes one ``PublishAdapter``-shaped
class registered via the ``poindexter.publish_adapters`` entry_point
group. The video pipeline's ``upload_to_platform`` Stage fans out to
every adapter that's configured + enabled and collects the results.

See :mod:`plugins.publish_adapter` for the Protocol contract.
"""
