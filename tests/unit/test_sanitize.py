"""Tests for HTML sanitization — the server-side XSS defense layer."""
from src.services.sanitize import sanitize_html, sanitize_text


class TestSanitizeHtml:
    def test_preserves_safe_html(self):
        html = "<p>Hello <strong>world</strong></p>"
        assert sanitize_html(html) == html

    def test_preserves_links(self):
        html = '<a href="https://example.com" title="link">click</a>'
        assert "https://example.com" in sanitize_html(html)

    def test_preserves_images(self):
        html = '<img src="https://example.com/img.png" alt="photo">'
        result = sanitize_html(html)
        assert "https://example.com/img.png" in result

    def test_strips_script_tags(self):
        html = '<p>safe</p><script>alert("xss")</script>'
        result = sanitize_html(html)
        assert "<script>" not in result
        assert "alert" not in result
        assert "<p>safe</p>" in result

    def test_strips_onerror_handler(self):
        html = '<img src=x onerror="alert(1)">'
        result = sanitize_html(html)
        assert "onerror" not in result

    def test_strips_onclick_handler(self):
        html = '<p onclick="steal()">click me</p>'
        result = sanitize_html(html)
        assert "onclick" not in result
        assert "click me" in result

    def test_strips_javascript_url(self):
        html = '<a href="javascript:alert(1)">click</a>'
        result = sanitize_html(html)
        assert "javascript:" not in result

    def test_strips_iframe(self):
        html = '<iframe src="https://evil.com"></iframe>'
        result = sanitize_html(html)
        assert "<iframe" not in result

    def test_strips_object_embed(self):
        html = '<object data="evil.swf"></object><embed src="evil.swf">'
        result = sanitize_html(html)
        assert "<object" not in result
        assert "<embed" not in result

    def test_strips_form_elements(self):
        html = '<form action="/steal"><input type="hidden" name="token" value="x"></form>'
        result = sanitize_html(html)
        assert "<form" not in result
        assert "<input" not in result

    def test_strips_style_tag(self):
        html = '<style>body { display: none }</style><p>ok</p>'
        result = sanitize_html(html)
        assert "<style>" not in result

    def test_preserves_tables(self):
        html = "<table><tr><td>data</td></tr></table>"
        result = sanitize_html(html)
        assert "<table>" in result
        assert "<td>data</td>" in result

    def test_preserves_lists(self):
        html = "<ul><li>one</li><li>two</li></ul>"
        assert sanitize_html(html) == html

    def test_preserves_headings(self):
        html = "<h2>Title</h2>"
        assert sanitize_html(html) == html

    def test_preserves_code_blocks(self):
        html = "<pre><code>let x = 1;</code></pre>"
        assert sanitize_html(html) == html

    def test_empty_string(self):
        assert sanitize_html("") == ""

    def test_none_returns_none(self):
        assert sanitize_html(None) is None

    def test_nested_xss(self):
        html = '<div><img src="x" onerror="eval(atob(\'YWxlcnQoMSk=\'))"></div>'
        result = sanitize_html(html)
        assert "onerror" not in result
        assert "eval" not in result

    def test_svg_xss(self):
        html = '<svg onload="alert(1)"><circle r="50"/></svg>'
        result = sanitize_html(html)
        assert "onload" not in result
        assert "<svg" not in result  # svg not in allowlist


class TestSanitizeText:
    def test_strips_all_html(self):
        assert sanitize_text('<script>alert("xss")</script>Hello') == "Hello"

    def test_strips_tags_preserves_text(self):
        assert sanitize_text("<b>bold</b> and <i>italic</i>") == "bold and italic"

    def test_preserves_plain_text(self):
        assert sanitize_text("Just plain text") == "Just plain text"

    def test_empty_string(self):
        assert sanitize_text("") == ""

    def test_none_returns_none(self):
        assert sanitize_text(None) is None

    def test_ampersands_and_entities(self):
        result = sanitize_text("Tom &amp; Jerry <b>show</b>")
        assert "Tom" in result
        assert "Jerry" in result
        assert "<b>" not in result
