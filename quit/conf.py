import logging

import os
from os import walk
from os.path import join, isfile
from quit.exceptions import MissingConfigurationError, InvalidConfigurationError
from quit.exceptions import UnknownConfigurationError
from quit.helpers import isAbsoluteUri
from rdflib import Graph, ConjunctiveGraph, Literal, Namespace, URIRef, BNode
from rdflib.plugins.parsers import notation3
from rdflib.namespace import RDF, NamespaceManager
from rdflib.util import guess_format
from urllib.parse import quote, urlparse, urlencode
from uritools import urisplit

logger = logging.getLogger('quit.conf')


class Feature:
    Unknown = 0
    Provenance = 1 << 0
    Persistence = 1 << 1
    GarbageCollection = 1 << 2
    All = Provenance | Persistence | GarbageCollection


class QuitConfiguration:
    """A class that keeps track of the relation between named graphs and files."""

    def __init__(
        self,
        configmode=None,
        configfile='config.ttl',
        features=None,
        repository=None,
        targetdir=None,
        namespace=None
    ):
        """The init method.

        This method checks if the config file is given and reads the config file.
        If the config file is missing, it will be generated after analyzing the
        file structure.
        """
        logger = logging.getLogger('quit.conf.QuitConfiguration')
        logger.debug('Initializing configuration object.')

        self.features = features
        self.configchanged = False
        self.sysconf = Graph()
        self.graphconf = None
        self.origin = None
        self.graphs = {}
        self.files = {}
        self.namespace = None

        self.quit = Namespace('http://quit.aksw.org/vocab/')
        self.nsMngrSysconf = NamespaceManager(self.sysconf)
        self.nsMngrSysconf.bind('', 'http://quit.aksw.org/vocab/', override=False)
        self.nsMngrGraphconf = NamespaceManager(self.sysconf)
        self.nsMngrGraphconf.bind('', 'http://quit.aksw.org/vocab/', override=False)

        try:
            self.__initstoreconfig(
                namespace=namespace,
                repository=repository,
                targetdir=targetdir,
                configfile=configfile,
                configmode=configmode
            )
        except InvalidConfigurationError as e:
            logger.error(e)
            raise e

        return

    def __initstoreconfig(self, namespace, repository, targetdir, configfile, configmode):
        """Initialize store settings."""
        if isAbsoluteUri(namespace):
            self.namespace = namespace
        else:
            raise InvalidConfigurationError(
                "Quit expects an absolute http(s) base namespace, {} is not absolute.".format(
                    namespace))

        if configfile and isfile(configfile):
            try:
                self.sysconf.parse(configfile, format='turtle')
            except notation3.BadSyntax:
                raise InvalidConfigurationError(
                    "Bad syntax. Configuration file could not be parsed. {}".format(configfile)
                )
            except PermissionError:
                raise InvalidConfigurationError(
                    "Configuration file could not be parsed. Permission denied. {}".format(
                        configfile
                    )
                )
            except Exception as e:
                raise UnknownConfigurationError(
                    "UnknownConfigurationError: {}".format(e)
                )

            self.configfile = configfile
        else:
            if not targetdir:
                raise InvalidConfigurationError('No target directory for git repo given')

        if configmode:
            self.setConfigMode(configmode)

        if targetdir:
            self.setRepoPath(targetdir)

        if repository:
            self.setGitOrigin(repository)

        return

    def initgraphconfig(self):
        """Initialize graph settings.

        Public method to initalize graph settings. This method will be run only once.
        """
        if self.graphconf is None:
            self.__initgraphconfig()

    def __initgraphconfig(self, repository=None, targetdir=None):
        """Initialize graph settings."""
        self.graphconf = Graph()
        configmode = self.getConfigMode()
        logger.debug("Graph Config mode is: {}".format(configmode))

        if configmode == 'localconfig':
            self.__initgraphsfromconf(self.configfile)
        elif configmode == 'repoconfig':
            remConfigFile = join(self.getRepoPath(), 'config.ttl')
            self.__initgraphsfromconf(remConfigFile)
        elif configmode == 'graphfiles':
            self.__initgraphsfromdir(self.getRepoPath())
        else:
            raise InvalidConfigurationError('This mode is not supported.', self.configmode)
        return

    def __initgraphsfromdir(self, repodir):
        """Init a repository by analyzing all existing files."""
        graphs = self.getgraphsfromdir(repodir)
        repopath = self.getRepoPath()

        for file, format in graphs.items():
            absgraphfile = os.path.join(repopath, file + '.graph')
            graphuri = self.__readGraphIriFile(absgraphfile)

            if graphuri and format == 'nquads':
                self.addgraph(file=file, graphuri=graphuri, format=format)
            elif graphuri is None and format == 'nquads':
                tmpgraph = ConjunctiveGraph(identifier='default')

                try:
                    tmpgraph.parse(source=os.path.join(repopath, file), format=format)
                except Exception:
                    logger.error(
                        "Could not parse graphfile {}. File skipped.".format(file)
                    )
                    continue

                namedgraphs = tmpgraph.contexts()
                founduris = []

                for graph in namedgraphs:
                    if not isinstance(graph, BNode) and str(graph.identifier) != 'default':
                        graphuri = graph.identifier
                        founduris.append(graphuri)

                if len(founduris) == 1:
                    self.addgraph(file=file, graphuri=graphuri, format=format)
                elif len(founduris) > 1:
                    logger.info("No named graph found. {} skipped.".format(file))

                elif len(founduris) < 1:
                    logger.info(
                        "More than one named graphs found. Can't decide. {} skipped.".format(
                            file
                        )
                    )

            elif format == 'nt':
                if graphuri:
                    self.addgraph(file=file, graphuri=graphuri, format=format)
                else:
                    logger.warning('No *.graph file found. ' + file + ' skipped.')

        try:
            self.__setgraphsfromconf()
        except InvalidConfigurationError as e:
            raise e

    def __initgraphsfromconf(self, configfile):
        """Init graphs with setting from config.ttl."""
        if not isfile(configfile):
            raise MissingConfigurationError("Configfile is missing {}".format(configfile))

        try:
            self.graphconf.parse(configfile, format='turtle')
        except Exception as e:
            raise InvalidConfigurationError(
                "Configfile could not be parsed {} {}".format(configfile, e)
            )

        # Get Graphs
        self.__setgraphsfromconf()

    def __readGraphIriFile(self, graphfile):
        """Search for a graph uri in graph file and return it.

        Args:
            graphfile: String containing the path of a graph file

        Returns:
            graphuri: String with the graph URI
        """
        try:
            with open(graphfile, 'r') as f:
                graphuri = f.readline().strip()
        except FileNotFoundError:
            logger.debug("File not found {}".format(graphfile))
            return

        try:
            urlparse(graphuri)
            logger.debug("Graph URI {} found in {}".format(graphuri, graphfile))
        except Exception:
            graphuri = None
            logger.debug("No graph URI found in {}".format(graphfile))

        return graphuri

    def __setgraphsfromconf(self):
        """Set all URIs and file paths of graphs that are configured in config.ttl."""
        nsQuit = 'http://quit.aksw.org/vocab/'
        query = 'SELECT DISTINCT ?graphuri ?filename WHERE { '
        query += '  ?graph a <' + nsQuit + 'Graph> . '
        query += '  ?graph <' + nsQuit + 'graphUri> ?graphuri . '
        query += '  ?graph <' + nsQuit + 'graphFile> ?filename . '
        query += '}'
        result = self.graphconf.query(query)

        repopath = self.getRepoPath()

        for row in result:
            filename = str(row['filename'])
            format = guess_format(filename)
            if format not in ['nt', 'nquads']:
                break

            graphuri = str(row['graphuri'])

            graphFile = join(repopath, filename)

            if isfile(graphFile):
                # everything is fine
                pass
            else:
                try:
                    open(graphFile, 'a+').close()
                except PermissionError:
                    raise InvalidConfigurationError(
                        "Permission denied. Can't create file {} in repo {}".format(
                            graphFile,
                            self.getRepoPath()
                        )
                    )
                except FileNotFoundError:
                    raise InvalidConfigurationError(
                        "File not found. Can't create file {} in repo {}".format(
                            graphFile,
                            self.getRepoPath()
                        )
                    )
                except Exception as e:
                    raise UnknownConfigurationError(
                        "Can't create file {} in repo {}. Error: {}".format(
                            graphFile,
                            self.getRepoPath(),
                            e
                        )
                    )

            graphuri = URIRef(graphuri)

            # we store which named graph is serialized in which file
            self.graphs[graphuri] = filename
            # and furthermore we assume that one file can contain data of more
            # than one named graph and so we store for each file a set of graphs
            if filename in self.files:
                self.files[filename]['graphs'].append(graphuri)
            else:
                self.files[filename] = {
                    'serialization': format,
                    'graphs': [graphuri]
                }

        return

    def addgraph(self, graphuri, file, format=None):
        self.graphconf.add((self.quit[quote(graphuri)], RDF.type, self.quit.Graph))
        self.graphconf.add((self.quit[quote(graphuri)], self.quit.graphUri, URIRef(graphuri)))
        self.graphconf.add((self.quit[quote(graphuri)], self.quit.graphFile, Literal(file)))
        if format is not None:
            self.graphconf.add((self.quit[quote(graphuri)], self.quit.hasFormat, Literal(format)))

        return

    def removegraph(self, graphuri):
        self.graphconf.remove((self.quit[urlencode(graphuri)], None, None))

        return

    def getConfigMode(self):
        """Get the mode how Quit-Store detects RDF files and named graphs.

        Returns:
            A string containig the mode.
        """
        nsQuit = 'http://quit.aksw.org/vocab/'
        property = URIRef(nsQuit + 'configMode')

        for s, p, o in self.sysconf.triples((None, property, None)):
            return str(o)

        return 'graphfiles'

    def getRepoPath(self):
        """Get the path of Git repository from configuration.

        Returns:
            A string containig the path of the git repo.
        """
        nsQuit = 'http://quit.aksw.org/vocab/'
        storeuri = URIRef('http://my.quit.conf/store')
        property = URIRef(nsQuit + 'pathOfGitRepo')

        for s, p, o in self.sysconf.triples((None, property, None)):
            return str(o)

    def getDefaultBranch(self):
        """Get the default branch on the Git repository from configuration.

        Returns:
            A string containing the branch name.
        """
        nsQuit = 'http://quit.aksw.org/vocab/'
        storeuri = URIRef('http://my.quit.conf/store')
        property = URIRef(nsQuit + 'defaultBranch')

        for s, p, o in self.sysconf.triples((None, property, None)):
            return str(o)

        return "master"

    def getGlobalFile(self):
        """Get the graph file which should be used for unassigned graphs.

        Returns:
            The filename of the graph file where unassigned graphs should be stored.
        """
        nsQuit = 'http://quit.aksw.org/vocab/'
        storeuri = URIRef('http://my.quit.conf/store')
        property = URIRef(nsQuit + 'globalFile')

        for s, p, o in self.sysconf.triples((None, property, None)):
            return str(o)

    def getOrigin(self):
        """Get the URI of Git remote from configuration."""
        nsQuit = 'http://quit.aksw.org/vocab/'
        storeuri = URIRef('http://my.quit.conf/store')
        property = URIRef(nsQuit + 'origin')

        for s, p, o in self.sysconf.triples((storeuri, property, None)):
            return str(o)

    def getgraphs(self):
        """Get all graphs known to conf.

        Returns:
            A list containig all graph uris as string,
        """
        graphs = []
        for graph in self.graphs:
            graphs.append(graph)

        return graphs

    def getfiles(self):
        """Get all files known to conf.

        Returns:
            A list containig all files as string,
        """
        files = []
        for file in self.files:
            files.append(file)

        return files

    def getfileforgraphuri(self, graphuri):
        """Get the file for a given graph uri.

        Args:
            graphuri: A String of the named graph

        Returns:
            A string of the path to the file asociated with named graph
        """
        if isinstance(graphuri, str):
            graphuri = URIRef(graphuri)
        for uri, filename in self.graphs.items():
            if uri == graphuri:
                return filename

        return

    def getgraphurifilemap(self):
        """Get the dictionary of graphuris and their files.

        Returns:
            A dictionary of graphuris and information about their files.
        """

        return self.graphs

    def getserializationoffile(self, file):
        """Get the file for a given graph uri.

        Args:
            file: A String of a file path

        Returns:
            A string containing the RDF serialization of file
        """
        if file in self.files:
            return self.files[file]['serialization']

        return

    def getgraphuriforfile(self, file):
        """Get the file for a given graph uri.

        Args:
            file: A String of a file path

        Returns:
            A set containing strings of graph uris asociated to that file
        """
        if file in self.files:
            return self.files[file]['graphs']

        return []

    def getgraphsfromdir(self, path=None):
        """Get the files that are part of the repository (tracked or not).

        Returns:
            A list of filepathes.
        """
        if path is None:
            path = self.getRepoPath()

        exclude = set(['.git'])

        graphfiles = {}
        for dirpath, dirs, files in walk(path):
            dirs[:] = [d for d in dirs if d not in exclude]
            for filename in files:

                format = guess_format(join(dirpath, filename))
                if format is not None:
                    graphfiles[filename] = format

        return graphfiles

    def hasFeature(self, flags):
        return flags == (self.features & flags)

    def setConfigMode(self, mode):
        self.sysconf.remove((None, self.quit.configMode, None))
        self.sysconf.add((self.quit.Store, self.quit.configMode, Literal(mode)))

        return

    def setGitOrigin(self, origin):
        self.sysconf.remove((None, self.quit.origin, None))
        self.sysconf.add((self.quit.Store, self.quit.origin, Literal(origin)))

        return

    def setRepoPath(self, path):
        self.sysconf.remove((None, self.quit.pathOfGitRepo, None))
        self.sysconf.add((self.quit.Store, self.quit.pathOfGitRepo, Literal(path)))

        return

    def getBindings(self):
        ns = Namespace('http://quit.aksw.org/vocab/')
        q = """SELECT DISTINCT ?prefix ?namespace WHERE {{
            {{
                ?ns a <{binding}> ;
                    <{predicate_prefix}> ?prefix ;
                    <{predicate_namespace}> ?namespace .
            }}
        }}""".format(
            binding=ns['Binding'], predicate_prefix=ns['prefix'],
            predicate_namespace=ns['namespace']
        )

        result = self.sysconf.query(q)
        return [(row['prefix'], row['namespace']) for row in result]
