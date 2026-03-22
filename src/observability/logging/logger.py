import logging
import sys
import structlog


def setup_logging() -> None:
 

    logging.basicConfig(
        format="%(message)s",
        level=logging.INFO,
    )


    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars, 
            structlog.processors.TimeStamper(fmt="iso"), 
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,

            structlog.processors.JSONRenderer(),  
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """
    Get a structured logger instance.
    """
    return structlog.get_logger(name)