"""Strict text envelope for the iPhone email-to-Intercom receiver.

This is format isolation, not sender authentication. Only matched payload text
may be spoken; never fall back to an email body on missing/invalid matches.
"""
import re

START = "HOMESAFE-7D3B9A21:"
END = ":END-7D3B9A21"
PATTERN = r"(?<=HOMESAFE-7D3B9A21:)[^\r\n]{1,1500}?(?=:END-7D3B9A21)"


def encode(message):
    text = " ".join(message.split())
    if not text or len(text) > 1500 or START in text or END in text:
        raise ValueError("Invalid announcement payload")
    return START + text + END


def extract(body):
    return re.findall(PATTERN, body)
