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
import subprocess
import time
import re
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
    def __init__(self, repo, path, toplevel=True):
        self.repo = repo
        self.path = Path(path)
        # False for images found in a subfolder of the images repo;
        # only toplevel images are considered 'official' (and synced)
        self.toplevel = toplevel
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
            print(f"WARNING unreadable path {self}", file=sys.stderr)
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
        # where 'share' stores images, relative to the public repo
        # empty means the toplevel; can be set per host
        self.share_subdir = the_config.value('frisbee', 'share_subdir')
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

    @staticmethod
    def _walk_ndz(directory, recursive) -> Iterator[Path]:
        """
        yields the (plain or symlink) *.ndz files in this directory,
        and, if recursive, in its subfolders (symlinks to folders are
        not followed); the order is deterministic
        """
        directory = Path(directory)
        found = (directory.rglob if recursive else directory.glob)(
            f"*{SUFFIX}")
        yield from sorted(found)

    def _iterate_images(self, directory, predicate, recursive=False) \
            -> Iterator[ImagePath]:
        """
        returns an iterator on ImagePath objects
        in this directory so that bool(predicate(image_path)) is True
        the images repo is meant to be scanned recursively, so that
        old images can be stored in subfolders and still be found;
        images that sit right in the directory are flagged as toplevel
        """
        directory = Path(directory)
        for filename in self._walk_ndz(directory, recursive):
            image_path = ImagePath(
                self, filename, toplevel=(filename.parent == directory))
            if predicate(image_path):
                yield image_path

    def locate_all_images(self, radical, look_in_global) -> List[ImagePath]:
        """
        all the readable matches, best first: toplevel images (cwd and
        images repo) come first, newest first among them; images that are
        stored in subfolders of the repo come next, newest first
        """
        match = lambda image_path: (image_path.radical == radical
                                    or str(image_path) == radical)
        candidates = list(self._iterate_images(".", match))
        if look_in_global:
            candidates += list(self._iterate_images(
                self.public, match, recursive=True))
        candidates = [candidate for candidate in candidates
                      if candidate.readable]
        # the same file can be seen twice, e.g. if cwd is the repo
        candidates = self._remove_duplicates(candidates)
        candidates.sort(key=lambda info: (info.toplevel, info.mtime),
                        reverse=True)
        return candidates

    @staticmethod
    def _remove_duplicates(image_paths):
        """
        the same entry can be reached through several paths,
        for example if the current directory is the images repo
        """
        seen = set()
        result = []
        for image_path in image_paths:
            path = image_path.path
            key = (path.parent.resolve(), path.name)
            if key not in seen:
                seen.add(key)
                result.append(image_path)
        return result

    @staticmethod
    def _warn_competing(radical, winner, others):
        """
        always warn when a name can be found in several places
        goes to stderr as resolve prints its result on stdout
        """
        print(f"WARNING: {len(others) + 1} candidates for image {radical}",
              file=sys.stderr)
        print(f"  * {winner.path}   (selected)", file=sys.stderr)
        for other in others:
            print(f"    {other.path}", file=sys.stderr)

    def locate_image(self, radical, look_in_global) -> ImagePath:
        all_matches = self.locate_all_images(radical, look_in_global)
        if not all_matches:
            return None
        if len(all_matches) > 1:
            self._warn_competing(radical, all_matches[0], all_matches[1:])
        return all_matches[0]


    def _search_clusters(self, *, show_dot, show_public,
                         cluster_predicate, image_predicate,
                         sort_clusters=None, reverse=False):
        """
        search for images in either '.' and public dirs,
        (the public dir is scanned recursively)
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
                self.public, image_predicate, recursive=True))
        candidates = self._remove_duplicates(candidates)
        clusters_hash = defaultdict(list)
        for image in candidates:
            if not image.readable:
                # already reported by ImagePath._infos
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


    def _in_subfolders(self, image_path, subfolders) -> bool:
        """
        is this image stored in one of these subfolders (or below)
        of the public repo
        """
        try:
            parts = image_path.path.parent.relative_to(self.public).parts
        except ValueError:
            # not in the public repo, e.g. in the current directory
            return False
        for subfolder in subfolders:
            wanted = Path(subfolder).parts
            if wanted and parts[:len(wanted)] == wanted:
                return True
        return False

    def images(self, focus, sort_by, reverse,           # pylint: disable=r0913
               labeled, public_only, narrow, and_or, show_all=False,
               subfolders=()):
        """
        subfolders: names of subfolders of the public repo (possibly nested,
        like old/sub); if given, only the images stored in these are shown,
        and that includes the nested ones
        """
        for subfolder in subfolders:
            if not (self.public / subfolder).is_dir():
                print(f"WARNING: no subfolder {subfolder} in {self.public}",
                      file=sys.stderr)
        show_dot = not public_only
        # images that are found twice, e.g. when running from within
        # the public repo, are only shown once by _search_clusters
        show_public = True
        long_format = not narrow
        # is a given cluster filtered by focus
        def in_focus(cluster):
            # an empty focus means to show all
            if not focus:
                return True
            def match_one(filtr):
                # rough : no regexp, just find the filter or not
                return any(str(image_path).find(filtr) >= 0 for image_path in cluster)
            # default is 'or'
            if and_or == 'and':
                return all(match_one(filtr) for filtr in focus)
            else:
                return any(match_one(filtr) for filtr in focus)

        def select_labeled(cluster):
            if not labeled:
                return True
            # else we select only the ones that have an alias
            return cluster.aliases >= 1
        def select_toplevel(cluster):
            # by default, images stored in subfolders of the repo
            # (i.e. old stuff) are not shown, unless they have an alias
            # at toplevel, as the cluster then has a toplevel member
            if show_all or subfolders:
                return True
            return any(image_path.toplevel for image_path in cluster)
        def select_subfolders(cluster):
            if not subfolders:
                return True
            return any(self._in_subfolders(image_path, subfolders)
                       for image_path in cluster)
        def cluster_predicate(cluster):
            return (select_toplevel(cluster) and select_subfolders(cluster)
                    and select_labeled(cluster) and in_focus(cluster))
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
        def matching_images(cluster):
            return [image for image in cluster
                    if image.radical == focus or str(image) == focus]

        def cluster_matches(cluster):
            return bool(matching_images(cluster))

        def cluster_rank(cluster):
            # a cluster that is matched by a toplevel image beats the ones
            # that are only matched in subfolders, whatever their dates
            return (any(image.toplevel for image in matching_images(cluster)),
                    cluster.regular.mtime)

        true_function = lambda x: True
        clusters = self._search_clusters(
            show_dot=True, show_public=True,
            cluster_predicate=cluster_matches,
            image_predicate=true_function,
            sort_clusters=cluster_rank, reverse=False)
        if not clusters:
            return 1
        if len(clusters) > 1:
            # the best cluster is the last one
            self._warn_competing(
                focus,
                matching_images(clusters[-1])[0],
                [image for cluster in reversed(clusters[:-1])
                 for image in matching_images(cluster)])
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


    def _subfolders(self) -> List[str]:
        """
        the subfolders of the public repo, as paths relative to it
        """
        relatives = (path.relative_to(self.public)
                     for path in self.public.rglob("*")
                     if path.is_dir() and not path.is_symlink())
        return sorted(str(relative) for relative in relatives
                      if not any(part.startswith('.')
                                 for part in relative.parts))

    def _suggest_archiving(self, old_target, previous_links, all_links):
        """
        print a suggestion, but do not do anything: these links
        (previous_links) used to lead to old_target and have been redirected,
        so that old_target is not the latest any longer
        all_links are all the links that share is dealing with
        the user is in charge of picking a subfolder where to move it
        """
        others = [str(path) for path in self._walk_ndz(self.public, True)
                  if path.is_symlink() and path not in all_links
                  and path.resolve() == old_target]
        size = ImagePath.bytes2human(old_target.stat().st_size)
        names = ', '.join(path.name for path in previous_links)
        print(f"SUGGESTION: {names} used to lead to "
              f"{old_target.name} ({size}), that is now superseded")
        if others:
            print("  but these other aliases still point at it "
                  "(they would dangle if it was moved):")
            for other in others:
                print(f"    {other}")
        subfolders = self._subfolders()
        if subfolders:
            print("  Subfolders available in "
                  f"{self.public}: {', '.join(sub + '/' for sub in subfolders)}")
        else:
            print(f"  No subfolder exists yet in {self.public}")
        print(f"  To archive it, something like: "
              f"mv {old_target} {self.public}/<SUBFOLDER>/")

    def _plan_links(self, aliases, radical, dest_dir, force):
        """
        turns the -a specs into a dict {link filename: target filename}
        all links are bare names, living in dest_dir like the image
        a spec is either LINK, that points at the image, or LINK:TARGET,
        where TARGET is another link of the same command, or something that
        already exists in dest_dir; so the order of the specs does not matter

        returns None, after having explained why, if this is not doable;
        nothing is done at this stage
        """
        def fail(message):
            print(f"Invalid alias: {message} - nothing done")

        links = {}
        for spec in aliases:
            link, _, target = spec.partition(':')
            target = target or radical
            if not link or '/' in link or '/' in target:
                return fail(f"{spec!r} is not LINK or LINK:TARGET with bare "
                            f"names")
            if link in links:
                return fail(f"{link} is given twice")
            if link == radical:
                return fail(f"{link} is the name of the image itself")
            links[link] = target
        for link, target in links.items():
            if target != radical and target not in links \
                    and not (dest_dir / (target + SUFFIX)).exists():
                return fail(f"the target {target} of {link} is neither "
                            f"the image, nor another alias, nor an existing "
                            f"image in {dest_dir}")
            path = dest_dir / (link + SUFFIX)
            if path.exists() and not path.is_symlink() and not force:
                return fail(f"{path} is a plain file, not a symlink "
                            f"(use --force to replace it)")

        def next_hop(name):
            """what a name leads to, once all links are in place"""
            if name in links:
                return links[name]
            path = dest_dir / (name + SUFFIX)
            if name == radical or not path.is_symlink():
                return None
            # an existing symlink, that is not touched by this command
            target = path.parent / path.readlink()
            if (target.parent.resolve() != dest_dir.resolve()
                    or not target.name.endswith(SUFFIX)):
                return None
            return target.name[:-len(SUFFIX)]

        for link in links:
            seen = set()
            name = link
            while name is not None:
                if name in seen:
                    return fail(f"{link} would lead back to itself")
                seen.add(name)
                name = next_hop(name)
        return {link + SUFFIX: target + SUFFIX
                for link, target in links.items()}

    @staticmethod
    def _links_depth(links, filename):
        """number of hops in links before reaching something else"""
        depth = 0
        while filename in links:
            filename = links[filename]
            depth += 1
        return depth

    def share(self, image, aliases,         # pylint:disable=r0912,r0913,r0914,r0915
              dry_run, force, clean, subdir=None) -> OsRetcod:
        """
        A utility to install one or several images in the shared repo

        Parameters:
          image: a local image name (not searched in global repo)
            to promote in the public repo
          aliases: a list of LINK or LINK:TARGET, to make the installed image
            visible under other names, possibly through several levels like
            ubuntu -> ubuntu-26 -> the image; see _plan_links
          subdir: a subfolder of the public repo where to store the image
            (and the alias); defaults to the share_subdir config entry,
            which is empty (i.e. the toplevel) unless set for this host
            e.g. on the preplab so that images are not deleted by a sync

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
                print("WARNING: without sudo you can only dry-run", file=sys.stderr)

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
        removes = []
        chmods = []

        if subdir is None:
            subdir = self.share_subdir
        subdir_path = Path(subdir) if subdir else Path()
        if subdir_path.is_absolute() or '..' in subdir_path.parts:
            print(f"Invalid subfolder {subdir} - must be relative to "
                  f"{self.public} - ignored")
            return 1
        dest_dir = self.public / subdir_path

        origin = image_path.path
        destination = dest_dir / (radical + SUFFIX)
        toplevel_destination = self.public / (radical + SUFFIX)
        skip = False
        if destination.exists() and not force:
            print(f"WARNING: Destination {destination} already exists "
                  f"- ignored", file=sys.stderr)
        elif (dest_dir != self.public and not force
              and (toplevel_destination.exists()
                   or toplevel_destination.is_symlink())):
            # it would be shadowed by the toplevel one
            print(f"WARNING: {toplevel_destination} already exists, "
                  f"it would take precedence over {destination} "
                  f"- ignored (use --force to override)", file=sys.stderr)
            skip = True
        else:
            moves.append((origin, destination))  # append a tuple
            chmods.append(destination)

        # file names of the links to create, and what they lead to
        links = {}
        if aliases and not skip:
            links = self._plan_links(aliases, radical, dest_dir, force)
            if links is None:
                return 1
        elif not aliases:
            print("WARNING: you are sharing an image without an alias",
                  file=sys.stderr)
        link_paths = {dest_dir / filename: target
                      for filename, target in links.items()}
        # what the links are about to replace, to suggest archiving it
        previous = {path: path.readlink() for path in link_paths
                    if path.is_symlink()}
        old_targets = defaultdict(list)
        for path in previous:
            old_targets[path.resolve()].append(path)
        chmods += list(link_paths)

        if clean:
            # item # 0 is the one selected for being moved
            for match in matches:
                if match.path == origin:
                    continue
                if match.is_official:
                    continue
                removes.append(match.path)

        matches.reverse()
        for index, match in enumerate(matches):
            prefix = '* ' if (index == len(matches) - 1) else '  '
            print(match._to_display(
                show_path=True, radical_width=len(radical), prefix=prefix))

        if links:
            print("Aliases once done, outermost first:")
            width = max(len(filename) for filename in links)
            for filename in sorted(links, key=lambda name: (
                    -self._links_depth(links, name), name)):
                before = previous.get(dest_dir / filename)
                if before is None:
                    note = "(new)"
                elif str(before) == links[filename]:
                    note = "(unchanged)"
                else:
                    note = f"(was -> {before})"
                print(f"  {filename:{width}} -> {links[filename]}  {note}")

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
                destination.parent.mkdir(parents=True, exist_ok=True)
                print(f"Moving {origin} -> {destination}")
                # the library folder may be shared over sshfs
                # and in that case using rename does not work 
                # because it is across filesystems
                # origin.rename(destination)
                subprocess.run(["mv", str(origin), str(destination)],
                               check=False)

        for symlink, target in link_paths.items():
            # all in the same folder, so that a relative link is enough
            if dry_run:
                show_dry_run(f"ln -sf {target} {symlink}")
            else:
                print(f"Creating symlink {symlink} -> {target}")
                # atomic, so that the name is never missing
                temporary = symlink.with_name(symlink.name + ".tmp-share")
                if temporary.is_symlink():
                    temporary.unlink()
                temporary.symlink_to(target)
                temporary.replace(symlink)

        for chmod in chmods:
            if dry_run:
                show_dry_run(f"chmod a+r {chmod}")
                continue
            omod = chmod.stat().st_mode
            nmod = omod | 0o444
            print(f"new mode for {chmod} is {oct(nmod)}")
            chmod.chmod(nmod)

        for old_target, previous_links in old_targets.items():
            if (old_target.exists()
                    and old_target != destination.resolve()
                    and old_target.parent == self.public.resolve()):
                self._suggest_archiving(
                    old_target, previous_links, set(link_paths))

        return 0


    def fix_links(self, dry_run) -> OsRetcod:
        """
        repair the symlinks that dangle because the image they point at
        was moved to another folder of the repo, typically to a subfolder
        when archiving it

        such a link is moved right next to the plain file, where its
        bare-name relative target is valid again

        - only plain files are looked for: a link that leads to another
          link is reported, not moved, as it is probably meant to be
          retargeted (or it would drag e.g. default.ndz out of toplevel)
        - nothing is done if the name has several candidates, or if the
          name is already taken in the destination folder

        dry_run defaults to True unless running as root, like share
        return 0 once done, 1 if not allowed
        """
        is_root = root_privileges()
        if dry_run is None:
            dry_run = not is_root
            if dry_run:
                print("WARNING: without sudo you can only dry-run",
                      file=sys.stderr)
        if not dry_run and not is_root:
            print("You need to run rhubarbe-fix-links under sudo")
            return 1

        everything = list(self._walk_ndz(self.public, True))
        # plain files, by name
        plains = defaultdict(list)
        for path in everything:
            if not path.is_symlink() and path.is_file():
                plains[path.name].append(path)
        dangling = [path for path in everything
                    if path.is_symlink() and not path.exists()]
        # computed upfront, as moving links around changes what is where
        link_names = {path.name for path in everything if path.is_symlink()}

        def show(path):
            return str(path.relative_to(self.public))

        fixed = 0
        pending = []
        for link in dangling:
            target = link.readlink()
            candidates = [path for path in plains.get(target.name, [])
                          if path.parent != link.parent]
            if len(candidates) == 1:
                new_link = candidates[0].parent / link.name
                if new_link.exists() or new_link.is_symlink():
                    pending.append(
                        (link, f"{show(new_link)} already exists"))
                    continue
                print(f"{'DRY-RUN: would move' if dry_run else 'Moving'} "
                      f"{show(link)} -> {show(new_link)}   "
                      f"(-> {target.name})")
                fixed += 1
                if not dry_run:
                    # bare name, whatever the previous target looked like
                    new_link.symlink_to(target.name)
                    link.unlink()
            elif candidates:
                pending.append((link, "several candidates: " + ", ".join(
                    show(path) for path in candidates)))
            elif target.name in link_names:
                pending.append(
                    (link, f"leads to the alias {target.name}, not to a "
                           f"plain file: retarget it, or move it next to "
                           f"that alias, by hand"))
            else:
                pending.append((link, f"no plain file {target.name} found"))

        for link, reason in pending:
            print(f"NOT FIXED {show(link)} -> {link.readlink()}: {reason}")
        print(f"{len(dangling)} dangling link(s), {fixed} "
              f"{'would be ' if dry_run else ''}fixed, "
              f"{len(pending)} left as is")
        return 0
