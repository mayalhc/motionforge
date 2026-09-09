"""mkdocs rewrites relative paths inside markdown links, but not inside raw
HTML. The screen recordings are <video> tags, so their src has to be fixed up
here or every page below the site root loads nothing."""
import re

SRC = re.compile(r'(<(?:video|source)\b[^>]*?\bsrc=")([^"]+)(")')


def on_page_content(html, page, config, files):
    url_depth = page.url.rstrip("/").count("/") + (1 if page.url.endswith("/") else 0)
    src_depth = page.file.src_uri.count("/")
    up = url_depth - src_depth
    if up <= 0:
        return html
    prefix = "../" * up

    def fix(m):
        target = m.group(2)
        if target.startswith((".", "/", "http:", "https:", "data:")):
            return m.group(0)
        return m.group(1) + prefix + target + m.group(3)

    return SRC.sub(fix, html)
