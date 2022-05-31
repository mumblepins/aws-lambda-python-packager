# -*- coding: utf-8 -*-
import logging

from colorama import (
    Fore,
    Style,
    init,
)

init()


# https://stackoverflow.com/questions/384076/how-can-i-color-python-logging-output
class _CustomLogFormatter(logging.Formatter):
    """Logging Formatter to add colors and count warning / errors"""

    log_format = "%(asctime)s - %(name)s|(%(filename)s:%(lineno)d) - %(levelname)s - %(message)s"

    FORMATS = {
        logging.DEBUG: Fore.CYAN + log_format + Fore.RESET,
        logging.INFO: Fore.BLUE + log_format + Fore.RESET,
        logging.WARNING: Fore.YELLOW + log_format + Fore.RESET,
        logging.ERROR: Fore.RED + log_format + Fore.RESET,
        logging.CRITICAL: Fore.RED + Style.BRIGHT + log_format + Fore.RESET,
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt)
        return formatter.format(record)
