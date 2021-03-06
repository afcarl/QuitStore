from tempfile import TemporaryDirectory
from pygit2 import init_repository, clone_repository, Signature
from os import path, walk
from os.path import join


def createCommit(repository, message=None):
    """Create a commit."""
    message = "Commit to temporary test repo"
    author = Signature('QuitStoreTest', 'quit@quit.aksw.org')
    comitter = Signature('QuitStoreTest', 'quit@quit.aksw.org')

    def _addAll(index, path, dir=''):
        for (dirpath, dirnames, filenames) in walk(path):
            if dirpath.startswith(join(repository.workdir, '.git')):
                continue
            for filename in filenames:
                index.add(join(dir, filename))

    index = repository.index
    index.read()
    _addAll(index, repository.workdir)

    # Create commit
    tree = index.write_tree()
    parents = []
    if not repository.is_empty:
        parents.append(repository.head.target)
    repository.create_commit('HEAD', author, comitter, message, tree, parents)


class TemporaryRepository(object):
    """A Git repository initialized in a temporary directory as a context manager.

    usage:
    with TemporaryRepository() as tempRepo:
        print("workdir:", tempRepo.workdir)
        print("path:", tempRepo.path)
        index = repo.index
        index.read()
        index.add("...")
        index.write()
        tree = index.write_tree()
        repo.create_commit('HEAD', author, comitter, message, tree, [])
    """

    def __init__(self, is_bare=False, clone_from_repo=None):
        self.temp_dir = TemporaryDirectory()
        if clone_from_repo:
            self.repo = clone_repository(clone_from_repo.path, self.temp_dir.name)
        else:
            self.repo = init_repository(self.temp_dir.name, is_bare)

    def __enter__(self):
        return self.repo

    def __exit__(self, type, value, traceback):
        self.temp_dir.cleanup()


class TemporaryRepositoryFactory(object):
    """A factory class for temporary repositories."""

    author = Signature('QuitStoreTest', 'quit@quit.aksw.org')
    comitter = Signature('QuitStoreTest', 'quit@quit.aksw.org')

    def withEmptyGraph(self, graphUri):
        """Give a TemporaryRepository() initialized with a an empty graph (and one commit)."""
        return self.withGraph(graphUri)

    def withGraph(self, graphUri, graphContent=None):
        """Give a TemporaryRepository() initialized with a graph with the given content (and one commit)."""
        tmpRepo = TemporaryRepository()

        # Add a graph.nq and a graph.nq.graph file
        with open(path.join(tmpRepo.repo.workdir, "graph.nq"), "w") as graphFile:
            if graphContent:
                graphFile.write(graphContent)

        # Set Graph URI to http://example.org/
        with open(path.join(tmpRepo.repo.workdir, "graph.nq.graph"), "w") as graphFile:
            graphFile.write(graphUri)

        # Add and Commit the empty graph
        index = tmpRepo.repo.index
        index.read()
        index.add("graph.nq")
        index.add("graph.nq.graph")
        index.write()

        # Create commit
        tree = index.write_tree()
        message = "init"
        tmpRepo.repo.create_commit('HEAD', self.author, self.comitter, message, tree, [])

        return tmpRepo

    def withGraphs(self, graphUriContentDict):
        """Give a TemporaryRepository() initialized with a dictionary of graphUris and content (nq)."""
        tmpRepo = TemporaryRepository()
        index = tmpRepo.repo.index
        index.read()

        i = 0
        for graphUri, graphContent in sorted(graphUriContentDict.items()):
            filename = 'graph_{}.nq'.format(i)
            with open(path.join(tmpRepo.repo.workdir, filename), "w") as graphFile:
                if graphContent:
                    graphFile.write(graphContent)

            # Set Graph URI to http://example.org/
            with open(path.join(tmpRepo.repo.workdir, filename + ".graph"), "w") as graphFile:
                graphFile.write(graphUri)

            # Add and Commit the empty graph
            index.add(filename)
            index.add(filename + '.graph')
            i += 1

        index.write()

        # Create commit
        tree = index.write_tree()
        message = "init"
        tmpRepo.repo.create_commit('HEAD', self.author, self.comitter, message, tree, [])

        return tmpRepo


def assertResultBindingsEqual(self, listA, listB, queryVariables=['s', 'p', 'o', 'g']):
    """Assert that two lists of SPARQL JSON result bindings are equal."""
    def sort(item):
        key = ""
        for spog in queryVariables:
            key += item[spog]['value'] + item[spog]['type']
        return key

    self.assertListEqual(sorted(listA, key=sort), sorted(listB, key=sort))
