import scheduler_config
import scheduler_logging
import trade_signal_generator as tsg


def test_scheduler_config_exports_are_backward_compatible():
    assert tsg.DEFAULT_SESSION_RULES is scheduler_config.DEFAULT_SESSION_RULES
    assert tsg.TRADE_PLAN_COLUMNS == scheduler_config.TRADE_PLAN_COLUMNS
    assert tsg.METADATA_COLUMNS == scheduler_config.METADATA_COLUMNS
    assert tsg.TRIGGER_COMMAND == scheduler_config.TRIGGER_COMMAND


def test_scheduler_logging_exports_are_backward_compatible():
    assert tsg.LOGGER is scheduler_logging.LOGGER
    assert tsg.setup_logging is scheduler_logging.setup_logging
    assert tsg.timed_task is scheduler_logging.timed_task
