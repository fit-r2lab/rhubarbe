"""
The contents of one directory that holds images
"""

# c0111 no docstrings yet
# w0201 attributes defined outside of __init__
# w1202 logger & format
# w0703 catch Exception
# r1705 else after return
# pylint: disable=c0111, r1705

import sys
import os
import time
import re
import glob
from pathlib import Path
#from itertools import chain
from collections import defaultdict

from typing import Iterator, List

from rhubarbe.config import Config
from rhubarbe.singleton import Singleton

# to indicate that 0 is OK and others are KO
OsRetcod = int

DEBUG = False

# use __ instead of == because = ruins bash completion
SUFFIX = ".ndz"
SAVING = 'saving'
SEP = '__'
TIME_FORMAT = "%Y-%m-%d@%H-%M"

SEP_RE_PATTERN = "[-_=]{2}"
DATE_RE_PATTERN = "[0-9]{4}-[0-9]{2}-[0-9]{2}@[0-9]{2}.[0-9]{2}"
# this will be fed into .format() - needs double {{ and }}
HOSTNAME_RE_PATTERN_F = "{regularname}[0-9]{{2}}"

SEP_GLOB_PATTERN = "[-_=]" * 2
DATE_GLOB_PATTERN = "[0-9-@]" * 16
HOSTNAME_GLOB_PATTERN_F = "{regularname}[0-9][0-9]"


def root_privileges():
    # surprisingly, even when running under sudo we have all 3 set to 0
    real, effective, saved = os.getresuid()         # pylint: disable=e1101
    is_root = (real == 0) and (effective == 0) and (saved == 0)
    return is_root


class ImagePath:                                 # pylint: disable=r0902, r0903
    def __init__(self, repo, path):
        self.repo = repo
        self.path = Path(path)
        # pylint: disable=w0212
        self.radical = self.repo._radical_part(self.path)
        self.stem = self.path.stem
        self.is_official = self.radical == self.stem
        # just in case
        self.readable = None
        self._infos()

    def _infos(self):
        try:
            with self.path.open():
                pass
            self.readable = True
        except OSError:
            print(f"WARNING unreadable path {self}")
            self.readable = False
            self.mtime = 0
            self.size = 0
            return
        stat = self.path.stat()
        self.mtime = stat.st_mtime
        self.size = stat.st_size
        self.inode = stat.st_ino
        self.is_alias = self.path.is_symlink()

    def __str__(self):
        return str(self.path)

    def __repr__(self):
        return self._to_display(show_path=True)

    def __eq__(self, other):
        return self.path == other.path

    def _to_display(self, show_path, radical_width=None, prefix=''):
        # date/time on 10+1+5 = 16
        # 1 sep
        # size on 8
        # 2 sep
        # radical on 30 : 56
        # 2 sep
        radical_width = (radical_width if radical_width is not None
                         else len(self.radical))
        result = prefix
        date = time.strftime("%Y-%m-%d %H:%M", time.localtime(self.mtime))
        if not self.is_alias:
            result += f"{date:<16s} "
            result += f"{self.bytes2human(self.size):>8s}"
        else:
            result += f"{'':<16s} "
            result += f"{'aka':>8s}"
        result += f"  {self.radical:{radical_width}}"
        if show_path and not self.is_alias:
            result += f"  {self.path}"
        return result

    @staticmethod
    def bytes2human(size, repr_format="{value:.2f}{symbol}"):
        """
        >>> bytes2human(10000)
        '9K'
        >>> bytes2human(100001221)
        '95M'
        """
        symbols = ('B', 'K', 'M', 'G', 'T', 'P', 'E', 'Z', 'Y')
        prefix = {}
        for i, symbol in enumerate(symbols[1:]):
            prefix[symbol] = 1 << (i+1)*10
        for symbol in reversed(symbols[1:]):
            if size >= prefix[symbol]:
                value = float(size) / prefix[symbol]
                return repr_format.format(value=value, symbol=symbol)
        return repr_format.format(symbol=symbols[0], value=size)


class ImageCluster:
    """
    a cluster is a set of ImagePath that share the same actual file
    typically it is one plain file and n-1 symlinks
    """
    def __init__(self, repo, image_paths):
        self.repo = repo
        self.image_paths = image_paths
        inodes = {path.inode for path in self.image_paths}
        if len(inodes) != 1:
            print("OOPS, wrong clustering!")
        # the image_path that is no symlink/alias
        self._regular = None
        self.aliases = 0
        for path in self.image_paths:
            if not path.is_alias:
                self._regular = path
            else:
                self.aliases += 1
        if not self._regular:
            print(f"OOPS - no regular file found in cluster "
                  f"with {self.image_paths[0]} that has {self.aliases} aliases")
        self.image_paths.sort(
            key=lambda image_path: image_path.mtime, reverse=True)


    def __iter__(self):
        return iter(self.image_paths)

    def __repr__(self):
        return repr(self.regular)

    @property
    def regular(self):
        return self._regular or self.image_paths[0]


    def _display(self, long_format, radical_width):
        # pylint: disable=w0212
        print(self.regular._to_display(long_format, radical_width))
        for image_path in self.image_paths:
            if image_path == self.regular:
                continue
            print(image_path._to_display(long_format, radical_width))

    def _max_radical(self):
        return max(len(image.radical) for image in self)


class ImagesRepo(metaclass=Singleton):
    def __init__(self):
        the_config = Config()
        self.public = Path(the_config.value('frisbee', 'images_dir'))
        self.default_name = the_config.value('frisbee', 'default_image')
        # for building hostname patterns
        regularname = the_config.value('testbed', 'regularname')
        self._radical_re_matcher = re.compile(
            SEP.join([
                f"{SAVING}",
                f"{regularname}[0-9][0-9]",
                f"{DATE_RE_PATTERN}",
                f"(?P<radical>.+)",
                ]))


    def default(self) -> str:
        return str(self.public / self.default_name)

    @staticmethod
    def where_to_save(nodename, radical) -> str:
        """
        given a nodename, plus a user-provided image name
        computes the actual path where to store an image
        as of 3.0.7, root is deemed to not be likely to build images
        so no more special case for uid==0
        """
        stem = SEP.join([SAVING, nodename,
                         time.strftime(TIME_FORMAT), radical])
        return stem + SUFFIX

    def _radical_part(self, path):
        """
        incoming can be a filename, possibly without an extension
        the saving__ blabla is extracted and only the part
        given at save-time is returned
        it is kind of the reverse of where_to_save
        """
        stem = Path(path).stem
        match = self._radical_re_matcher.match(stem)
        if match:
            return match.group('radical')
        return stem

    def _iterate_images(self, directory, predicate) -> Iterator[ImagePath]:
        """
        returns an iterator on ImagePath objects
        in this directory so that bool(predicate(image_path)) is True
        """
        directory = Path(directory)
        #2019 nov 22 - somehow this is broken
        #for filename in directory.glob(f"*{SUFFIX}"):
        for filename in glob.glob(f"{directory}/*{SUFFIX}"):
            image_path = ImagePath(self, filename)
            if predicate(image_path):
                yield image_path

    def locate_all_images(self, radical, look_in_global) -> List[ImagePath]:
        match = lambda image_path: (image_path.radical == radical
                                    or str(image_path) == radical)
        candidates = list(self._iterate_images(".", match))
        if look_in_global:
            candidates += list(self._iterate_images(self.public, match))
        candidates = [candidate for candidate in candidates if candidate.readable]
        candidates.sort(key=lambda info: info.mtime, reverse=True)
        return candidates


    def locate_image(self, radical, look_in_global) -> ImagePath:
        all_matches = self.locate_all_images(radical, look_in_global)
        return all_matches[0] if all_matches else None


    def _search_clusters(self, *, show_dot, show_public,
                         cluster_predicate, image_predicate,
                         sort_clusters=None, reverse=False):
        """
        search for images in either '.' and public dirs,
        that satisfy the image predicate
        then groups them by clusters
        filters out the ones that don't match
        sort the clusters
        displays them
        """
        candidates = []
        if show_dot:
            candidates += list(self._iterate_images(".", image_predicate))
        if show_public:
            candidates += list(self._iterate_images(
                self.public, image_predicate))
        clusters_hash = defaultdict(list)
        for image in candidates:
            if not image.readable:
                print(f"WARNING: ignoring unreadable image {image}")
                continue
            clusters_hash[image.inode].append(image)
        clusters = [ImageCluster(self, images)
                    for (inode, images) in clusters_hash.items()]
        clusters = [cluster for cluster in clusters
                    if cluster_predicate(cluster)]
        # sort_clusters is applied to ImageCluster objects
        if sort_clusters:
            clusters.sort(key=sort_clusters, reverse=reverse)
        return clusters


    def images(self, focus, sort_by, reverse,           # pylint: disable=r0913
               labeled, public_only, narrow):
        show_dot = not public_only
        show_public = True
        long_format = not narrow
        # is a given cluster filtered by focus
        def in_focus(cluster):
            # an empty focus means to show all
            if not focus:
                return True
            # in this context a cluster is just a list of infos
            for image_path in cluster:
                for filtr in focus:
                    # rough : no regexp, just find the filter or not
                    if str(image_path).find(filtr) >= 0:
                        return True
            return False
        def select_labeled(cluster):
            if not labeled:
                return True
            # else we select only the ones that have an alias
            return cluster.aliases >= 1
        def cluster_predicate(cluster):
            return select_labeled(cluster) and in_focus(cluster)
        def cluster_key(cluster):
            if sort_by == 'size':
                return cluster.regular.size
            elif sort_by == 'date':
                return cluster.regular.mtime
            elif sort_by == 'name':
                return cluster.regular.radical
            else:
                return 1

        clusters = self._search_clusters(
            show_dot=show_dot, show_public=show_public,
            cluster_predicate=cluster_predicate,
            image_predicate=lambda x: True,
            sort_clusters=cluster_key, reverse=reverse)
        # pylint: disable=w0212
        radical_width = max((cluster._max_radical() for cluster in clusters),
                            default=0)
        for cluster in clusters:
            # pylint: disable=w0212
            cluster._display(long_format, radical_width)


    def resolve(self, focus, verbose):
        """
        default output is just **one** readable path
        in verbose mode, the whole cluster(s) are displayed
        """
        def cluster_matches(cluster):
            return any(image.radical == focus
                       or str(image) == focus for image in cluster)

        true_function = lambda x: True
        clusters = self._search_clusters(
            show_dot=True, show_public=True,
            cluster_predicate=cluster_matches,
            image_predicate=true_function,
            sort_clusters=lambda cluster: cluster.regular.mtime, reverse=False)
        if not clusters:
            return 1
        if not verbose:
            print(clusters[-1].regular.path)
            return 0
        # verbose mode: list all clusters like in rimages
        # pylint: disable=w0212
        radical_width = max((cluster._max_radical() for cluster in clusters),
                            default=0)
        for cluster in clusters:
            # pylint: disable=w0212
            cluster._display(True, radical_width)
        return 0


    def share(self, image, alias,           # pylint:disable=r0912,r0913,r0914
              dry_run, force, clean) -> OsRetcod:
        """
        A utility to install one or several images in the shared repo

        Parameters:
          image: a local image name (not searched in global repo)
            to promote in the public repo
          alias: a name for the installed image

        image gets renamed into the radical from the origin name,
        i.e. with the extra saving__*blabla removed

        (.) identity
        root is allowed to override destination, not regular users
        thus this needs to be run under sudo, only privileged slices
        are so allowed

        # return 0 if all goes well, 1 otherwise
        """

        is_root = root_privileges()
        # compute dry_run if not set on the command line
        if dry_run is None:
            dry_run = not is_root
            if dry_run:
                print("WARNING: without sudo you can only dry-run")

        if not dry_run and not is_root:
            print("You need to run rhubarbe-share under sudo")
            return 1

        # if that's a filename, let's use it
        image_path = ImagePath(self, image)
        if not image_path.readable:
            image_path = self.locate_image(image, look_in_global=is_root)
        if not image_path:
            print(f"Could not find image {image} - ignored")
            return 1
        radical = image_path.radical
        matches = self.locate_all_images(radical, look_in_global=is_root)

        # first pass
        # compute a list of tuples (origin, destination)
        # all these objects are plain pathlib.Path instances
        moves = []     # list of tuples oldname, newname
        symlinks = []  # list of tuples plainfile, symlink
        removes = []
        chmods = []

        origin = image_path.path
        destination = self.public / (radical + SUFFIX)
        if destination.exists() and not force:
            print(f"WARNING: Destination {destination} already exists - ignored")
        else:
            moves.append((origin, destination))  # append a tuple
            chmods.append(destination)

        if alias:
            symlink = self.public / (alias + SUFFIX)
            symlinks.append((destination, symlink))
            chmods.append(symlink)
        else:
            print("WARNING: you are sharing an image without an alias")

        if clean:
            # item # 0 is the one selected for being moved
            for match in matches:
                if match == origin:
                    continue
                if match.is_official:
                    continue
                removes.append(match.path)

        matches.reverse()
        for index, match in enumerate(matches):
            prefix = '* ' if (index == len(matches) - 1) else '  '
            print(match._to_display(
                show_path=True, radical_width=len(radical), prefix=prefix))

        def show_dry_run(*args):
            print("DRY-RUN: would do: ", end="")
            print(*args)

        for remove in removes:
            if dry_run:
                show_dry_run(f"rm {remove}")
            else:
                print(f"Removing {remove}")
                remove.unlink()

        for origin, destination in moves:
            if dry_run:
                show_dry_run(f"mv {origin} {destination}")
            else:
                print(f"Moving {origin} -> {destination}")
                # the library folder may be shared over sshfs
                # and in that case using rename does not work 
                # because it is across filesystems
                # origin.rename(destination)
                command = f"mv {str(origin)} {str(destination)}"
                os.system(command)

        for plainfile, symlink in symlinks:
            if dry_run:
                show_dry_run(f"ln -sf {plainfile} {symlink}")
            else:
                print(f"Creating symlink {symlink} -> {plainfile}")
                if symlink.exists():
                    symlink.unlink()
                symlink.symlink_to(plainfile)

        for chmod in chmods:
            if dry_run:
                show_dry_run(f"chmod a+r {chmod}")
                continue
            omod = chmod.stat().st_mode
            nmod = omod | 0o444
            print(f"new mode for {chmod} is {oct(nmod)}")
            chmod.chmod(nmod)


        return 0
