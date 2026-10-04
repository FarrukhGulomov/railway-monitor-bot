"""Bot tokeni loglarga tushmasligi kerak (httpx URL'ni INFO'da log qiladi)."""

import logging


def test_httpx_does_not_log_telegram_urls_at_info(bot_module, caplog):
    secret = "123456789:AAFsecrettokenvalue"
    with caplog.at_level(logging.INFO):
        logging.getLogger("httpx").info(
            'HTTP Request: POST https://api.telegram.org/bot%s/sendMessage "HTTP/1.1 200 OK"', secret
        )
        logging.getLogger("httpcore.http11").debug("send_request_headers %s", secret)
    assert secret not in caplog.text


def test_httpx_and_httpcore_levels_are_warning_or_higher(bot_module):
    for name in ("httpx", "httpcore"):
        assert logging.getLogger(name).level >= logging.WARNING
