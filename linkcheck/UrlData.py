"""Base URL handler"""
# Copyright (C) 2000,2001  Bastian Kleineidam
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 675 Mass Ave, Cambridge, MA 02139, USA.

import sys, re, urlparse, urllib, time, traceback, socket, select
from urllib import splituser, splithost, splitport
#try:
#    from linkcheck import DNS
#except ImportError:
#    print >>sys.stderr, "You have to install PyDNS from http://pydns.sf.net/"
#    raise SystemExit
from linkcheck import DNS
DNS.DiscoverNameServers()

import Config, StringUtil, linkcheck, linkname, test_support, timeoutsocket
from linkparse import LinkParser
from debuglevels import *
debug = Config.debug

# helper function for internal errors
def internal_error ():
    print >> sys.stderr, linkcheck._("""\n********** Oops, I did it again. *************

You have found an internal error in LinkChecker. Please write a bug report
at http://sourceforge.net/tracker/?func=add&group_id=1913&atid=101913
or send mail to %s and include the following information:
1) The URL or file you are testing
2) Your commandline arguments and/or configuration.
3) The system information below.

If you disclose some information because its too private to you thats ok.
I will try to help you nontheless (but you have to give me *something*
I can work with ;).
""") % Config.Email
    type,value = sys.exc_info()[:2]
    print >> sys.stderr, type, value
    traceback.print_exc()
    print_app_info()
    print >> sys.stderr, linkcheck._("\n******** LinkChecker internal error, bailing out ********")
    sys.exit(1)


def print_app_info ():
    import os
    print >> sys.stderr, linkcheck._("System info:")
    print >> sys.stderr, Config.App
    print >> sys.stderr, "Python %s on %s" % (sys.version, sys.platform)
    for key in ("LC_ALL", "LC_MESSAGES",  "http_proxy", "ftp_proxy"):
        value = os.getenv(key)
        if value is not None:
            print >> sys.stderr, key, "=", `value`


def get_absolute_url (urlName, baseRef, parentName):
    """Search for the absolute url to detect the link type. This does not
       join any url fragments together! Returns the url in lower case to
       simplify urltype matching."""
    if urlName and ":" in urlName:
        return urlName.lower()
    elif baseRef and ":" in baseRef:
        return baseRef.lower()
    elif parentName and ":" in parentName:
        return parentName.lower()
    return ""


# we catch these exceptions, all other exceptions are internal
# or system errors
ExcList = [
   IOError,
   ValueError, # from httplib.py
   linkcheck.error,
   DNS.Error,
   timeoutsocket.Timeout,
   socket.error,
   select.error,
]

if hasattr(socket, "sslerror"):
    ExcList.append(socket.sslerror)

# regular expression for port numbers
is_valid_port = re.compile(r"\d+").match

class UrlData:
    "Representing a URL with additional information like validity etc"

    def __init__ (self,
                  urlName,
                  recursionLevel,
                  config,
                  parentName = None,
                  baseRef = None,
                  line = 0,
                  column = 0,
		  name = ""):
        self.urlName = urlName
        self.anchor = None
        self.recursionLevel = recursionLevel
        self.config = config
        self.parentName = parentName
        self.baseRef = baseRef
        self.errorString = linkcheck._("Error")
        self.validString = linkcheck._("Valid")
        self.warningString = None
        self.infoString = None
        self.valid = 1
        self.url = None
        self.line = line
        self.column = column
        self.name = name
        self.dltime = -1
        self.dlsize = -1
        self.checktime = 0
        self.cached = 0
        self.urlConnection = None
        self.extern = (1, 0)
        self.data = None
        self.has_content = 0
        url = get_absolute_url(self.urlName, self.baseRef, self.parentName)
        # assume file link if no scheme is found
        self.scheme = url.split(":", 1)[0] or "file"

    def setError (self, s):
        self.valid=0
        self.errorString = linkcheck._("Error")+": "+s

    def setValid (self, s):
        self.valid=1
        self.validString = linkcheck._("Valid")+": "+s

    def isHtml (self):
        return 0

    def setWarning (self, s):
        if self.warningString:
            self.warningString += "\n"+s
        else:
            self.warningString = s

    def setInfo (self, s):
        if self.infoString:
            self.infoString += "\n"+s
        else:
            self.infoString = s

    def copyFrom (self, urlData):
        self.errorString = urlData.errorString
        self.validString = urlData.validString
        self.warningString = urlData.warningString
        self.infoString = urlData.infoString
        self.valid = urlData.valid
        self.dltime = urlData.dltime


    def buildUrl (self):
        if self.baseRef:
            if ":" not in self.baseRef:
                self.baseRef = urlparse.urljoin(self.parentName, self.baseRef)
            self.url = urlparse.urljoin(self.baseRef, self.urlName)
        elif self.parentName:
            self.url = urlparse.urljoin(self.parentName, self.urlName)
        else: 
            self.url = self.urlName
        # unquote url
        self.url = urllib.unquote(self.url)
        # split into (modifiable) list
        self.urlparts = list(urlparse.urlsplit(self.url))
        # check userinfo@host:port syntax
        self.userinfo, host = splituser(self.urlparts[1])
        x, port = splitport(host)
        if port is not None and not is_valid_port(port):
            raise linkcheck.error(linkcheck._("URL has invalid port number %s")\
                                  % str(port))
        # set host lowercase and without userinfo
        self.urlparts[1] = host.lower()
        self.anchor = self.urlparts[4]


    def logMe (self):
        debug(BRING_IT_ON, "logging url")
        self.config.incrementLinknumber()
        if self.config["verbose"] or not self.valid or \
           (self.warningString and self.config["warnings"]):
            self.config.log_newUrl(self)


    def check (self):
        try:
            self._check()
        except KeyboardInterrupt:
            raise
        except (socket.error, select.error):
            # on Unix, ctrl-c can raise
            # error: (4, 'Interrupted system call')
            type, value = sys.exc_info()[:2]
            if type!=4:
                raise
        except test_support.Error:
            raise
        except:
            internal_error()


    def _check (self):
        debug(BRING_IT_ON, "Checking", self)
        if self.recursionLevel and self.config['wait']:
            debug(BRING_IT_ON, "sleeping for", self.config['wait'], "seconds")
            time.sleep(self.config['wait'])
        t = time.time()
        # check syntax
        debug(BRING_IT_ON, "checking syntax")
        if not self.urlName or self.urlName=="":
            self.setError(linkcheck._("URL is null or empty"))
            self.logMe()
            return
        try:
	    self.buildUrl()
            self.extern = self._getExtern()
        except tuple(ExcList):
            type, value, tb = sys.exc_info()
            debug(HURT_ME_PLENTY, "exception", traceback.format_tb(tb))
            self.setError(str(value))
            self.logMe()
            return

        # check the cache
        debug(BRING_IT_ON, "checking cache")
        if self.config.urlCache_has_key(self.getCacheKey()):
            self.copyFrom(self.config.urlCache_get(self.getCacheKey()))
            self.cached = 1
            self.logMe()
            return

        # apply filter
        debug(BRING_IT_ON, "extern =", self.extern)
        if self.extern[0] and (self.config["strict"] or self.extern[1]):
            self.setWarning(
                  linkcheck._("outside of domain filter, checked only syntax"))
            self.logMe()
            return

        # check connection
        debug(BRING_IT_ON, "checking connection")
        try:
            self.checkConnection()
            if self.anchor and self.config["anchors"]:
                self.checkAnchors(self.anchor)
        except tuple(ExcList):
            type, value, tb = sys.exc_info()
            debug(HURT_ME_PLENTY, "exception",  traceback.format_tb(tb))
            self.setError(str(value))

        # check content
        warningregex = self.config["warningregex"]
        if warningregex and self.valid:
            debug(BRING_IT_ON, "checking content")
            try:  self.checkContent(warningregex)
            except tuple(ExcList):
                type, value, tb = sys.exc_info()
                debug(HURT_ME_PLENTY, "exception",  traceback.format_tb(tb))
                self.setError(str(value))

        self.checktime = time.time() - t
        # check recursion
        debug(BRING_IT_ON, "checking recursion")
        if self.allowsRecursion():
            try: self.parseUrl()
            except tuple(ExcList):
                type, value, tb = sys.exc_info()
                debug(HURT_ME_PLENTY, "exception",  traceback.format_tb(tb))
                self.setError(str(value))
        # check content size
        self.checkSize()
        # close
        self.closeConnection()
        self.logMe()
        debug(BRING_IT_ON, "caching")
        self.putInCache()


    def closeConnection (self):
        # brute force closing
        if self.urlConnection is not None:
            try: self.urlConnection.close()
            except: pass
            # release variable for garbage collection
            self.urlConnection = None


    def putInCache (self):
        cacheKey = self.getCacheKey()
        if cacheKey and not self.cached:
            self.config.urlCache_set(cacheKey, self)
            self.cached = 1


    def getCacheKey (self):
        # use that the host is lowercase
        if self.urlparts:
            return urlparse.urlunsplit(self.urlparts)
        return None


    def checkConnection (self):
        self.urlConnection = urllib.urlopen(self.url)


    def allowsRecursion (self):
        # note: isHtml() might not be working if valid is false, so be
        # sure to test it first.
        return self.valid and \
               self.isHtml() and \
               not self.cached and \
               self.recursionLevel < self.config["recursionlevel"] and \
               not self.extern[0]


    def checkAnchors (self, anchor):
        debug(HURT_ME_PLENTY, "checking anchor", anchor)
        if not (self.valid and anchor and self.isHtml()):
            return
        h = LinkParser(self.getContent(), {'a': ['name']})
        for cur_anchor,line,column,name,base in h.urls:
            if cur_anchor == anchor:
                return
        self.setWarning(linkcheck._("anchor #%s not found") % anchor)


    def _getExtern (self):
        if not (self.config["externlinks"] or self.config["internlinks"]):
            return (0, 0)
        # deny and allow external checking
        Config.debug(HURT_ME_PLENTY, "Url", self.url)
        if self.config["denyallow"]:
            for entry in self.config["externlinks"]:
                Config.debug(HURT_ME_PLENTY, "Extern entry", entry)
                match = entry['pattern'].search(self.url)
                if (entry['negate'] and not match) or \
                   (match and not entry['negate']):
                    return (1, entry['strict'])
            for entry in self.config["internlinks"]:
                Config.debug(HURT_ME_PLENTY, "Intern entry", entry)
                match = entry['pattern'].search(self.url)
                if (entry['negate'] and not match) or \
                   (match and not entry['negate']):
                    return (1, 0)
            return (0, 0)
        else:
            for entry in self.config["internlinks"]:
                Config.debug(HURT_ME_PLENTY, "Intern entry", entry)
                match = entry['pattern'].search(self.url)
                if (entry['negate'] and not match) or \
                   (match and not entry['negate']):
                    return (0, 0)
            for entry in self.config["externlinks"]:
                Config.debug(HURT_ME_PLENTY, "Extern entry", entry)
                match = entry['pattern'].search(self.url)
                if (entry['negate'] and not match) or \
                   (match and not entry['negate']):
                    return (1, entry['strict'])
            return (1,0)


    def getContent (self):
        """Precondition: urlConnection is an opened URL."""
        if not self.has_content:
            self.has_content = 1
            t = time.time()
            self.data = self.urlConnection.read()
            self.dltime = time.time() - t
            self.dlsize = len(self.data)
        return self.data


    def checkContent (self, warningregex):
        """if a warning expression was given, call this function to check it
           against the content of this url"""
        match = warningregex.search(self.getContent())
        if match:
            self.setWarning(linkcheck._("Found %s in link contents") % \
                            `match.group()`)


    def checkSize (self):
        """if a maximum size was given, call this function to check it
           against the content size of this url"""
        maxbytes = self.config["warnsizebytes"]
        if maxbytes is not None and self.dlsize >= maxbytes:
            self.setWarning(linkcheck._("Content size %s is larger than %s")%\
                         (StringUtil.strsize(self.dlsize),
                          StringUtil.strsize(maxbytes)))


    def parseUrl (self):
        # default parse type is html
        debug(BRING_IT_ON, "Parsing recursively into", self)
        self.parse_html();


    def getUserPassword (self):
        for auth in self.config["authentication"]:
            if auth['pattern'].match(self.url):
                return auth['user'], auth['password']
        return None,None


    def parse_html (self):
        # search for a possible base reference
        h = LinkParser(self.getContent(), {'base': ['href']})
        baseRef = None
        if len(h.urls)>=1:
            baseRef = h.urls[0][0]
            if len(h.urls)>1:
                self.setWarning(linkcheck._(
                "more than one <base> tag found, using only the first one"))
        h = LinkParser(self.getContent())
        for url,line,column,name,codebase in h.urls:
            if codebase:
                base = codebase
            else:
                base = baseRef
            self.config.appendUrl(GetUrlDataFrom(url,
                                  self.recursionLevel+1, self.config,
                                  parentName=self.url, baseRef=base,
                                  line=line, column=column, name=name))


    def parse_opera (self):
        # parse an opera bookmark file
        name = ""
        lineno = 0
        lines = self.getContent().splitlines()
        for line in lines:
            lineno += 1
            line = line.strip()
            if line.startswith("NAME="):
                name = line[5:]
            elif line.startswith("URL="):
                url = line[4:]
                if url:
                    self.config.appendUrl(GetUrlDataFrom(url,
           self.recursionLevel+1, self.config, self.url, None, lineno, name))
                name = ""


    def parse_text (self):
        """parse a text file with on url per line; comment and blank
           lines are ignored
           UNUSED and UNTESTED, just use linkchecker `cat file.txt`
        """
        lineno = 0
        lines = self.getContent().splitlines()
        for line in line:
            lineno += 1
            line = line.strip()
            if not line or line.startswith('#'): continue
            self.config.appendUrl(GetUrlDataFrom(line, self.recursionLevel+1,
                                  self.config, self.url, None, lineno, ""))


    def __str__ (self):
        return ("%s link\n"
	       "urlname=%s\n"
	       "parentName=%s\n"
	       "baseRef=%s\n"
	       "cached=%s\n"
	       "recursionLevel=%s\n"
	       "urlConnection=%s\n"
	       "line=%s\n"
               "column=%s\n"
	       "name=%s" % \
             (self.scheme, self.urlName, self.parentName, self.baseRef,
              self.cached, self.recursionLevel, self.urlConnection, self.line,
              self.column, self.name))


from FileUrlData import FileUrlData
from IgnoredUrlData import IgnoredUrlData, ignored_schemes_re
from FtpUrlData import FtpUrlData
from GopherUrlData import GopherUrlData
from HttpUrlData import HttpUrlData
from HttpsUrlData import HttpsUrlData
from MailtoUrlData import MailtoUrlData
from TelnetUrlData import TelnetUrlData
from NntpUrlData import NntpUrlData


def GetUrlDataFrom (urlName, recursionLevel, config, parentName=None,
                    baseRef=None, line=0, column=0, name=None):
    url = get_absolute_url(urlName, baseRef, parentName)
    # test scheme
    if url.startswith("http:"):
        klass = HttpUrlData
    elif url.startswith("ftp:"):
        klass = FtpUrlData
    elif url.startswith("file:"):
        klass = FileUrlData
    elif url.startswith("telnet:"):
        klass = TelnetUrlData
    elif url.startswith("mailto:"):
        klass = MailtoUrlData
    elif url.startswith("gopher:"):
        klass = GopherUrlData
    elif url.startswith("https:"):
        klass = HttpsUrlData
    elif url.startswith("nttp:") or \
         url.startswith("news:") or \
         url.startswith("snews:"):
        klass = NntpUrlData
    # application specific links are ignored
    elif ignored_schemes_re.search(url):
        klass = IgnoredUrlData
    # assume local file
    else:
        klass = FileUrlData
    return klass(urlName, recursionLevel, config, parentName, baseRef,
                 line=line, column=column, name=name)
