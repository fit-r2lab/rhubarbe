"""
The contents of one directory that holds images
"""

# c0111 no docstrings yet
# w0201 attributes defined outside of __init__
# w1202 logger & format
# w0703 catch Exception
# r1705 else after return
# pylint: disable=c0111, c0103

import sys
import os
import os.path
import glob
import time
import re

from rhubarbe.config import Config
from rhubarbe.singleton import Singleton

debug = False

# use __ instead of == because = ruins bash completion
saving_keyword = 'saving'
saving_sep = '__'
saving_time_format = "%Y-%m-%d@%H-%M"

sep_re_pattern = "[-_=]{2}"
date_re_pattern = "[0-9]{4}-[0-9]{2}-[0-9]{2}@[0-9]{2}.[0-9]{2}"
# this will be fed into .format() - needs double {{ and }}
hostname_re_pattern_f = "{regularname}[0-9]{{2}}"

sep_glob_pattern = "[-_=]" * 2
date_glob_pattern = "[0-9-@]" * 16
hostname_glob_pattern_f = "{regularname}[0-9][0-9]"


class ImagesRepo(metaclass=Singleton):
    def __init__(self):
        the_config = Config()
        self.repo = the_config.value('frisbee', 'images_dir')
        self.default_name = the_config.value('frisbee', 'default_image')
        # for building hostname patterns
        self.regularname = the_config.value('testbed', 'regularname')

    suffix = ".ndz"

    def default(self):
        return os.path.join(self.repo, self.default_name)

    def add_extension(self, file):
        return file + self.suffix

    def where_to_save(self, nodename, radical):
        """
        given a nodename, plus a user-provided image name
        computes the actual path where to store an image
        * behaviour depends on actual id:
          root stores in global repo, regular users store in '.'
        * name always contains nodename and date
        """
        parts = [saving_keyword, nodename,
                 time.strftime(saving_time_format), radical]
        base = saving_sep.join(parts) + self.suffix

        # root stores stuff directly in the repo
        if os.getuid() == 0:
            return os.path.join(self.repo, base)
        # regular users store where they are
        # a more sophisticated approach would be to split radical
        # to find about another directory, but well..
        return base

    def radical_part(self, incoming):
        """
        incoming can be a filename, possibly without an extension
        the saving__ blabla is extracted and only the part
        given at save-time is returned
        it is kind of the reverse of where_to_save
        """
        incoming = os.path.basename(incoming)
        incoming = os.path.splitext(incoming)[0]
        parts = [saving_keyword,
                 hostname_glob_pattern_f.format(regularname=self.regularname),
                 date_glob_pattern,
                 "(?P<radical>.+)"]
        glob_pattern = re.compile(saving_sep.join(parts))
        match = glob_pattern.match(incoming)
        if match:
            return match.group('radical')
        return incoming

    class ImageInfo:                              # pylint: disable=r0902,r0903
        def __init__(self, repo, filename):
            # a valid path
            self.repo = repo
            self.filename = filename
            self.fill_info()

        def fill_info(self):
            self.radical = self.repo.radical_part(self.filename)
            stat = os.stat(self.filename)
            self.mtime = stat.st_mtime
            self.size = stat.st_size
            self.basename = os.path.splitext(os.path.basename(self.filename))
            self.is_official = self.radical == self.basename
            self.is_latest = None

        def __str__(self):
            return "{}{} {} {}".format(
                "*" if self.is_latest else " ",
                "O" if self.is_official else " ",
                self.filename,
                ImagesRepo.bytes2human(self.size))

    def _locate_infos(self, input_name, look_in_global):
        """
        search an image from its name
        * input_name:   a filename, possibly witout extension,
          possibly without saving_blabla
        * look_in_global: do we search in the global repo or just in .

        returns a list of ImageInfo objects
        (filename, radical, is_default, is_latest)
        where
        * filename is a valid filename that can be open()
        * radical
        is_default is a boolean that says if this is the

        If the image name actually denotes a filename (i.e. with .ndz) then
        no completion takes place

        Otherwise, all 'saving' images are also considered
        and the latest one is picked
        """

        # when look_in_global is set, but we are already in the repo:
        # turn it off
        if (look_in_global and
                os.path.abspath(os.getcwd()) == os.path.abspath(self.repo)):
            look_in_global = False

        # existing filename ?
        candidate = input_name
        if os.path.exists(candidate):
            return [ImagesRepo.ImageInfo(self, candidate)]
        if look_in_global and not os.path.isabs(input_name):
            candidate = os.path.join(self.repo, input_name)
            if os.path.exists(candidate):
                return [ImagesRepo.ImageInfo(self, candidate)]

        # it's not a plain filename, so explore more options
        glob_pattern = (
            saving_keyword + saving_sep +
            hostname_glob_pattern_f.format(regularname=self.regularname) +
            saving_sep + date_glob_pattern + saving_sep + input_name)
        patterns = [self.add_extension(input_name),
                    self.add_extension(glob_pattern)]
        if debug:
            print("glob_pattern", glob_pattern)
        if look_in_global and not os.path.isabs(input_name):
            repo_file = os.path.join(self.repo, input_name)
            repo_pattern = os.path.join(self.repo, glob_pattern)
            patterns += [self.add_extension(repo_file),
                         self.add_extension(repo_pattern)]
        # run these patterns
        found = []
        for pattern in patterns:
            found += glob.glob(pattern)
        return [ImagesRepo.ImageInfo(self, filename) for filename in found]

    def _locate_infos_sorted(self, input_name, look_in_global=True):
        details = self._locate_infos(input_name, look_in_global)
        details.sort(key=lambda info: info.mtime, reverse=True)
        if details:
            details[0].is_latest = True                 # pylint: disable=w0201
        return details

    @staticmethod
    def is_root():
        # surprisingly, even when running under sudo we have all 3 set to 0
        real, effective, saved = os.getresuid()         # pylint: disable=e1101
        is_root = (real == 0) and (effective == 0) and (saved == 0)
        return is_root

    def locate_image(self, image_name, look_in_global=None):
        if look_in_global is None:
            look_in_global = self.is_root()
        infos = self._locate_infos_sorted(image_name, look_in_global)
        return infos[0].filename if infos else []

    @staticmethod
    def bytes2human(n, repr_format="{value:.3f} {symbol}"):
        """
        >>> bytes2human(10000)
        '9K'
        >>> bytes2human(100001221)
        '95M'
        """
        symbols = ('B', 'K', 'M', 'G', 'T', 'P', 'E', 'Z', 'Y')
        prefix = {}
        for i, s in enumerate(symbols[1:]):
            prefix[s] = 1 << (i+1)*10
        for symbol in reversed(symbols[1:]):
            if n >= prefix[symbol]:
                value = float(n) / prefix[symbol]
                return repr_format.format(value=value, symbol=symbol)
        return repr_format.format(symbol=symbols[0], value=n)

    # entry point for rhubarbe images
    def main(self, focus, verbose=False, sort_by='date', reverse=False):
        self.display(focus, verbose=verbose, sort_by=sort_by, reverse=reverse,
                     human_readable=True)

    # rhubarbe resolve
    def resolve(self, focus, verbose, reverse):
        for incoming in focus:
            infos = self._locate_infos_sorted(incoming)
            if not infos:
                print("Image {} not found".format(incoming), file=sys.stderr)
            else:
                if not verbose:
                    # main purpose is to print out the right name
                    for info in infos:
                        if info.is_latest:
                            print(info.filename)
                else:
                    print(8*'*', "image {}".format(incoming))
                    # it comes in the wrong order, latest first
                    if not reverse:
                        infos.reverse()
                    for info in infos:
                        print(info)

    def display(self, focus, verbose,        # pylint:disable=r0912,r0913,r0914
                sort_by, reverse, human_readable):
        # show available images in some sensible way
        #
        # (*) focus: a list of re patterns
        # (.) if empty, everything is displayed
        # (.) otherwise only files that match one of these names
        #        are displayed (together with their symlinks)
        # (*) sort_by, reverse: how to sort
        # (*) human_readable: bool, show raw size or use units like MB or Gb
        #
        # We want to show all the names attached to a given image
        # through symlinks;  this is why all the filenames are
        # grouped by clusters; there is one cluster per real image file
        #
        # so what we do is
        # (1) scan all files and gather them by clusters
        #     that point at the same file
        #     for this we use a (inode, mtime) tuple, same value = same file
        # (2) for each cluster, we distinguish between
        #     'single' clusters, i.e. an iamge file without any symlink
        #     'annotated' clusters have at least one symlink
        #
        # default behaviour is to show only annotated images
        # when verbose is turned on, all clusters are shown

        print("==================== Images repository {}".format(self.repo))
        if not verbose:
            print("==== use with --verbose to see images without a symlink")
        if focus:
            print("==== images matching any of", " ".join(focus))

        # gather one info per file, with
        # a key (inode, mtime, size) (same key = same file),
        # prefix (filename, no extension),
        # isalias
        infos = []
        for path in glob.glob("{}/*.ndz".format(self.repo)):
            prefix = os.path.basename(path).replace(".ndz", "")
            try:
                stat = os.stat(path)
            except Exception:                           # pylint: disable=w0703
                print("WARNING - Cannot stat {} (dangling symlink ?) - ignored"
                      .format(path))
                continue
            stat_tuple = (stat.st_size, stat.st_mtime, stat.st_ino, )
            info = {'stat_tuple': stat_tuple, 'prefix': prefix,
                    'isalias': os.path.islink(path)}
            infos.append(info)
        # gather by same file (same stat_tuple)
        clusters = {}
        for info in infos:
            stat_tuple = info['stat_tuple']
            clusters.setdefault(stat_tuple, [])
            clusters[stat_tuple].append(info)

        # sort infos in one cluster, so that real files show up first
        def sort_info(info):
            return info['isalias']
        for cluster in clusters.values():
            cluster.sort(key=sort_info)

        # sort clusters
        cluster_items = list(clusters.items())

        def sort_clusters(k_v):
            # k_v is a tuple with a key and a list of infos
            # k_v[0] is a key
            return k_v[0][0] if sort_by == 'size' \
                else k_v[0][1]
        cluster_items.sort(key=sort_clusters, reverse=reverse)

        # is a given cluster filtered by focus
        def in_focus(cluster, focus):
            # an empty focus means to show all
            if not focus:
                return True
            # in this context a cluster is just a list of infos
            for info in cluster:
                for filtr in focus:
                    # rough : no regexp, just find the filter or not
                    if info['prefix'].find(filtr) >= 0:
                        return True
            return False

        for stat_tuple, cluster in cluster_items:
            # skip the cluster if not in focus
            if not in_focus(cluster, focus):
                continue
            # without the verbose flag, show only clusters
            # that have at least one symlink
            if not verbose:
                # do we have at least one symlink ?
                if not any([info['isalias'] for info in cluster]):
                    # ignoring single clusters in non-verbose mode
                    continue
            shown = False
            for info in cluster:
                (size, mtime, _) = info['stat_tuple']
                print_size = size if not human_readable \
                    else ImagesRepo.bytes2human(size)
                date = time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))
                prefix = info['prefix']
                if not shown:
                    print("{:<18}{:>12}B {:40}"
                          .format(date, print_size, prefix))
                    shown = True
                else:
                    print("{:>31} {:40}".format("a.k.a.", prefix))

    def share(self, images, alias,           # pylint:disable=r0912,r0913,r0914
              dry_run, force, clean):
        """
        A utility to install one or several images in the shared repo

        Parameters:
          images: a list of image names (not searched in global repo)
            to transfer
          alias: a name for the installed image -
            only if a single image is provided

        Each provided image gets renamed into the radical of origin name,
        i.e. with the extra saving__*blabla removed

        (.) When a single image is given, and 'alias' is set,
            then a symlink from alias to radical is created

        (.) identity
        root is allowed to override destination, not regular users

        # return 0 if all goes well, 1 otherwise
        """

        # check args
        if len(images) > 1 and alias:
            print("alias can be specified only with one image")
            return 1

        is_root = self.is_root()
        # compute dry_run if not set on the command line
        if dry_run is None:
            dry_run = False if is_root else True

        # first pass
        # compute a list of tuples (origin, destination)
        for image in images:
            moves = []
            removes = []   # list of tuples
            symlinks = []  # list of tuples
            origin = self.locate_image(image, look_in_global=is_root)
            if not origin:
                print("Could not find image {} - ignored".format(image))
                continue
            matching_infos = \
                self._locate_infos_sorted(self.radical_part(origin),
                                          look_in_global=is_root)
            # at this point there's at most one that is marked official
            # but if the user has given us a full path, that's the one
            # that is going to be official, so must not be removed either
            for info in matching_infos:
                if info.filename == origin:
                    info.is_official = True              # pylint:disable=w0201

            radical = self.radical_part(origin)
            if not radical:
                print("WARNING: Could not guess radical part in {}\n"
                      "  you will need to give one with -d".format(origin))
                continue
            destination = os.path.join(self.repo, radical + ".ndz")
            if os.path.exists(destination) and not force:
                print("WARNING: Destination {} already exists - ignored"
                      .format(destination))
            else:
                moves.append((origin, destination))  # append a tuple
            if alias:
                symlinks.append((radical + ".ndz",   # ditto
                                 os.path.join(self.repo, alias + ".ndz")))
            else:
                print("Warning : share without an alias")
            if clean:
                # item # 0 is the one selected for being moved
                for info in matching_infos[1:]:
                    # among the other ones, DON't remove the official
                    # since it's useless - or harmful
                    if not info.is_official:
                        removes.append(info.filename)

            print(8*'*', image)
            matching_infos.reverse()
            for info in matching_infos:
                print(info)

            def do_dry_run(*args):
                print("DRY-RUN: would do")
                print(*args)

            for remove in removes:
                if dry_run:
                    do_dry_run("rm {}".format(remove))
                else:
                    print("Removing {}".format(remove))
                    os.unlink(remove)

            for origin, destination in moves:
                if dry_run:
                    do_dry_run("mv {} {}".format(origin, destination))
                else:
                    print("Moving {} -> {}".format(origin, destination))
                    os.rename(origin, destination)

            for origin, destination in symlinks:
                if dry_run:
                    do_dry_run("ln -sf {} {}".format(origin, destination))
                else:
                    print("Creating symlink {} -> {}".
                          format(destination, origin))
                    if os.path.exists(destination):
                        os.unlink(destination)
                    os.symlink(origin, destination)

        return 0
