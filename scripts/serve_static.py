"""
A static file server that answers range requests, for previewing real data.

``quarto preview`` is the better way to work on the site: it re-renders and
reloads as files change. It does not answer range requests, though, and the
site reads its observation files with them. Against the demo that costs
nothing, because the files are tiny. Against real data it means the browser
downloads a whole 120 MB file to draw one profile.

So this serves an already rendered site instead, with ranges, which is what
GitHub Pages does in production. Use it to check how much the site actually
fetches; use ``quarto preview`` for everything else.

    quarto render site
    uv run python scripts/serve_static.py site/_site

"""

import argparse
import io
import os
import re
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

#: ``bytes=0-99``, ``bytes=100-`` and ``bytes=-500`` are all legal.
RANGE_HEADER = re.compile(r"^bytes=(\d*)-(\d*)$")


class _Slice(io.RawIOBase):
    """
    A read-only view of the first ``length`` bytes from where a file is now.

    :class:`SimpleHTTPRequestHandler` copies whatever :meth:`send_head`
    returns until it reads end of file, so a partial response needs a handle
    that reports the end early rather than the real one.
    """

    def __init__(self, handle: io.BufferedReader, length: int) -> None:
        """
        :param handle: The open file, already seeked to the first byte wanted.
        :param length: How many bytes may be read from it.
        """
        self._handle = handle
        self._remaining = length

    def readable(self) -> bool:
        """:return: Always ``True``."""
        return True

    def readinto(self, buffer) -> int:
        """
        Fill ``buffer``, stopping at the end of the slice.

        :param buffer: The caller's writable buffer.
        :return: How many bytes were read, 0 at the end of the slice.
        """
        if self._remaining <= 0:
            return 0
        read = self._handle.readinto(memoryview(buffer)[: self._remaining])
        self._remaining -= read
        return read

    def close(self) -> None:
        """Close the underlying file as well."""
        self._handle.close()
        super().close()


class RangeRequestHandler(SimpleHTTPRequestHandler):
    """A :class:`SimpleHTTPRequestHandler` that honours the ``Range`` header."""

    #: Keep the connection open between requests. Reading one profile takes a
    #: handful of ranges, and a new connection for each of them would hide
    #: how cheap the reads really are.
    protocol_version = "HTTP/1.1"

    def end_headers(self) -> None:
        """Advertise range support on every response, as a static host does."""
        self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def send_head(self) -> Optional[io.RawIOBase]:
        """
        Answer a ``Range`` request with 206, or defer to the base class.

        :return: The body to send, or ``None`` when there is nothing to send.
        """
        requested = self.headers.get("Range")
        if requested is None:
            return super().send_head()

        match = RANGE_HEADER.match(requested.strip())
        path = self.translate_path(self.path)
        if match is None or os.path.isdir(path):
            return super().send_head()

        try:
            handle = open(path, "rb")
        except OSError:
            self.send_error(404, "File not found")
            return None

        size = os.fstat(handle.fileno()).st_size
        first, last = match.group(1), match.group(2)
        if first == "":
            # A suffix range: the last N bytes, which is how a parquet reader
            # asks for the footer before it knows anything else about a file.
            start, end = max(0, size - int(last or 0)), size - 1
        else:
            start = int(first)
            end = min(int(last), size - 1) if last else size - 1

        if start >= size or start > end:
            handle.close()
            self.send_response(416, "Requested Range Not Satisfiable")
            self.send_header("Content-Range", f"bytes */{size}")
            # Without this an HTTP/1.1 client waits for a body that is never
            # coming, and the request hangs rather than failing.
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None

        self.send_response(206, "Partial Content")
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()

        handle.seek(start)
        return _Slice(handle, end - start + 1)


def main() -> int:
    """
    Serve a directory until interrupted.

    :return: The process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", help="The directory to serve.")
    parser.add_argument("--port", type=int, default=4321, help="Default: 4321.")
    arguments = parser.parse_args()

    if not os.path.isdir(arguments.directory):
        parser.error(
            f"'{arguments.directory}' is not a directory. Run 'quarto render site' "
            "first."
        )

    handler = partial(RangeRequestHandler, directory=arguments.directory)
    with ThreadingHTTPServer(("127.0.0.1", arguments.port), handler) as server:
        print(f"Serving {arguments.directory} at http://127.0.0.1:{arguments.port}/")
        print("Range requests are answered here, the way GitHub Pages answers them.")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
