import http.cookiejar
import urllib.request
import urllib.error
from urllib.parse import urljoin, urldefrag
import re
import xml.etree.ElementTree as ET
from . import HtmlTree as HT

from typing import List, Set, Dict, Tuple, Text, Optional, AnyStr, Union, Iterator, \
    IO, Sequence, Iterable, cast

class _NoHttpRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, hdrs, newurl):
        return None


class HTTPException(Exception):
    """
    Raised when the requested page responds with HTTP code != 200
    """
    def __init__(self, code: int, page: 'Page') -> None:
        super().__init__("HTTP/" + str(code))

        self.code = code # type: int
        """ The HTTP status code """

        self.page = page # type: Page
        """ The (parsed) response page """

class TooManyRedirectsException(HTTPException):
    """
    Raised when the maximum number of redirects for this request have been exceeded
    """

class Browser:
    """
    Represents a virtual web browser.

    The Browser class is not very useful in itself, it only houses the cookie storage
    and default settings for individual requests.

    .. note:: MiniMech strives to be as stateless as possible.
        In contrast to e.g. :code:`WWW::Mechanize`, MiniMech will give you a
        new :any:`Page` object for every page you open and every link you follow.

        There is no such thing as a current page or a browser history.

    """

    def __init__(self, ua: str) -> None:
        """
        Constructs a new :any:`Browser` instance

        Parameters
        ----------
        ua : str
            Value of the :code:`User-Agent` header. This parameter is mandatory.
            If you want to be honest and upright, you'd include the name of your
            bot, e.g. ``'MiniMech Documentation Example / rgcjonas@gmail.com'``,
            but you can also impersonate a real-world browser.

        """


        self.default_headers = {'User-Agent': ua} # type: Dict[str, str]
        """
        List of headers sent with every request.

        By default, this contains the ``User-Agent`` header only.
        """


        self.cookiejar = http.cookiejar.CookieJar() # type: http.cookiejar.CookieJar
        """
        Cookie jar to use for all requests.

        By default, this is a newly constructed :any:`http.cookiejar.CookieJar`,
        but you may replace it with your own compatible object.
        """

    def open(self, url: str, *, additional_headers: Dict[str, str] = {},
             maximum_redirects: int = 10, data: bytes = None) -> 'Page':
        """
        Navigates to :code:`url` and returns a new :any:`Page` object.

        Parameters
        ----------
        url:
            The URL to open. This must be an absolute URL.

        additional_headers:
            Additional HTTP headers to append to this request

        maximum_redirects:
            Maximum number of redirects to follow for this request.

            In addition to standard HTTP/3xx redirects, MiniMech can follow serveral
            braindead redirect techniques that have been seen in the wild, e.g.
            HTTP/200 with `<meta http-equiv="Refresh" ...`

            Note: If your browser redirects something and MiniMech does not, then this
            is a bug and you should report it.

            If the allowed number of redirects is exceeded, a :any:`TooManyRedirectsException` will be thrown.
        data:
            POST data. If this is not ``None``, a POST request will be performed with the given
            data as content. If data is ``None`` (the default), a regular GET request is performed

        Notes
        -----

        *   Anything but a final HTTP/200 response will raise an exception.
        *   This function supports HTML responses only, and will try to parse anything it gets back as HTML.

        """

        opener = urllib.request.build_opener(_NoHttpRedirectHandler, urllib.request.HTTPCookieProcessor(self.cookiejar))

        request = urllib.request.Request(url, data=data)
        for header, val in self.default_headers.items():
            request.add_header(header, val)

        for header, val in additional_headers.items():
            request.add_header(header, val)

        try:
            response = opener.open(request) # type: Union[urllib.request.HTTPResponse, urllib.error.HTTPError, urllib.request.addinfourl]
        except urllib.error.HTTPError as r:
            response = r

        page = Page(self, response)
        redirect_to = None # type: Union[None, str]
        if (page.status in [301, 302, 303, 307]) and ('Location' in page.headers):
            # standard redirects
            redirect_to = page.headers['Location'].strip()

        if (page.status == 200) and (('Refresh' in page.headers)):
            # really brainded Refresh redirect
            match = re.fullmatch('\s*\d+\s*;\s*[uU][rR][lL]\s*=(.+)', page.headers['Refresh'])
            if match:
                redirect_to = match.group(1).strip()

                # referer change
                additional_headers = {**additional_headers, 'Referer': urldefrag(page.url).url}

        if ((page.status == 200) and not (page.document is None)):
            # look for meta tag
            for i in page.document.iter('meta'):
                h = str(i.get('http-equiv') or '')
                c = str(i.get('content') or '')
                match = re.fullmatch('\s*\d+\s*;\s*[uU][rR][lL]\s*=(.+)', c)
                if h.lower() == 'refresh' and match:
                    # still shitty meta redirect
                    redirect_to = match.group(1).strip()

                    # referer change
                    additional_headers = {**additional_headers, 'Referer': urldefrag(page.url).url}

        if redirect_to:
            if maximum_redirects > 0:
                return page.open(redirect_to, additional_headers=additional_headers, maximum_redirects=maximum_redirects-1)
            else:
                raise TooManyRedirectsException(page.status, page)
        elif page.status == 200:
            return page
        else:
            raise HTTPException(page.status, page)

class Page:
    """
    Represents a retrieved HTML page.

    .. note:: You don't want to construct a :any:`Page` instance yourself.

        Get it from  :any:`Browser.open` or :any:`Page.open`.

    Arguments
    ---------
    browser : Browser
        The :any:`Browser` instance

    response :
        A response object as retrieved from :any:`urllib.request.urlopen`

    """

    def __init__(self, browser: Browser, response) -> None:
        self.browser = browser
        """ The :any:`Browser` used to open this page  """

        self.status = response.getcode() # type: int
        """
        The HTTP status code received for this page (integer, read-only)
        """

        self.headers = response.info() # type: Dict[str, str]
        """
        The HTTP headers received with this page

        Note: This is a special kind of dictionary which is not case-sensitive
        """

        self.url = response.geturl() # type: str
        """ The URL to this page (str, read-only)"""

        self.response_bytes = response.read()
        """ The raw http response content, as a bytes-like object. """

        self.charset = HT.detect_charset(self.response_bytes, response.headers.get_content_charset())
        """
        The encoding used to decode the page (str).

        The encoding is determined by looking at the HTTP Content-Type header,
        byte order marks in the document and <meta> tags, and applying various
        rules as specified by WHATWG (e.g. treating ASCII as windows-1252).
        """

        self.document = HT.parsehtmlstr(str(self.response_bytes, self.charset, 'replace')) # type: HT.HtmlElement
        """
        The parsed document (:py:obj:`HT.HtmlElement`)
        """

        # fixup form page backreferences
        for f in self.forms:
            f.page = self

        # fixup hyperlink references
        for a in self.iterfind('.//a'):
            cast(HT.HtmlAnchorElement, a).page = self

    @property
    def baseuri(self) -> str:
        """
        The base URI which relative URLs are resolved against.

        This is always an absolute URL, even if it
        was specified as a relative URL in the <base> tag.

        .. note::

            This read-only property is calculated from the ``<base>`` tag(s) present
            in the document. If you change the ``<base>`` tag in the :any:`document`,
            you will change this property, too.
        """

        base = self.url

        # NOTE: at the moment, the html parser cannot fail and will
        # always return something. This is just defensive programming here
        if not (self.document is None): # pragma: no branch
            bases = self.document.findall('.//base[@href]')
            if len(bases) > 0:
                base = urljoin(self.url, (bases[0].get('href') or '').strip())

        return urldefrag(base).url

    @property
    def base(self) -> str:
        """ Alias for :any:`baseuri` """
        return self.baseuri

    @property
    def uri(self) -> str:
        """ Alias for :any:`url` (read-only str)"""
        return self.url

    def find(self, path:str='.//', namespaces:Dict[str,str]=None, **kwargs) -> Optional[HT.HtmlElement]:
        return self.document.find(path, namespaces, **kwargs)


    def findall(self, path:str='.//', namespaces:Dict[str,str]=None, **kwargs) -> List[HT.HtmlElement]:
        return self.document.findall(path, namespaces, **kwargs)

    def iterfind(self, path:str='.//', namespaces:Dict[str,str]=None, **kwargs) -> Iterator[HT.HtmlElement]:
        return self.document.iterfind(path, namespaces, **kwargs)

    @property
    def forms(self) -> 'HtmlFormsCollection':
        return HtmlFormsCollection(self.document.iterfind('.//form'))

    def open(self, url: str, **kwargs) -> 'Page':
        """
        Opens another page as if it was linked from the current page.

        Relative URLs are resolved properly, and a :code:`Referer` [sic] header
        is added (unless overriden in an ``additional_headers`` argument).
        All keyword arguments are forwarded to :any:`Browser.open`.
        """

        headers = { 'Referer': urldefrag(self.url).url }
        if ('additional_headers' in kwargs):
            for header, val in kwargs['additional_headers'].items():
                headers[header] = val

        kwargs['additional_headers'] = headers

        return self.browser.open(urljoin(self.baseuri, url), **kwargs)

class HtmlFormsCollection(Sequence[HT.HtmlFormElement]):
    """
    A list of <form> elements

    This is a sequence type (like a list), but you can also access elements by their name

    TODO: Example
    """
    def __init__(self, els: Iterable[HT.HtmlElement]) -> None:
        self.__backing_list = [cast(HT.HtmlFormElement, el) for el in els]

    # FIXME: key is Union[str,int] -> HtmlFormElement, but mypy doesn't like that
    def __getitem__(self, key):
        """
        Retrieve an option from the option list.

        In addition to slices and integers, you can also pass strings as key,
        then the option will be found by its value.
        """
        if isinstance(key, str):
            # find option by value
            for o in self.__backing_list:
                if o.name == key:
                    return o

            raise IndexError("No element with name '{0}' found".format(key))
        else:
            return self.__backing_list[key]

    def __len__(self) -> int:
        return len(self.__backing_list)
