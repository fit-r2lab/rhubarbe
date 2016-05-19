import os
import os.path
import glob
import time
import re

from rhubarbe.config import Config
from rhubarbe.singleton import Singleton

# use __ instead of == because = ruins bash completion
saving_sep = '__'
saving_time_format = "%Y-%m-%d@%H-%M"

class ImagesRepo(metaclass = Singleton):
    def __init__(self):
        the_config = Config()
        self.repo = the_config.value('frisbee', 'images_dir')
        self.name = the_config.value('frisbee', 'default_image')

    suffix = ".ndz"

    def default(self):
        return os.path.join(self.repo, self.name)

    def add_extension(self, file):
        return file + self.suffix

    def locate_image(self, image, look_in_global=True):
        # absolute path : just use image
        candidates = [ image, self.add_extension(image) ]
        if look_in_global and not os.path.isabs(image):
            repo_image = os.path.join(self.repo, image)
            candidates += [ repo_image, self.add_extension(repo_image) ]
        for candidate in candidates:
            if os.path.exists(candidate):
                return candidate

    def where_to_save(self, nodename, name_from_cli):
        """
        given a nodename, plus an option user-provided image name (may be None)
        computes the actual path where to store an image
        * behaviour depends on actual id (root stores in global repo, regular users store in '.')
        * name always contains nodename and date
        """
        parts = ['saving', nodename, time.strftime(saving_time_format)]
        if name_from_cli:
            parts.append(name_from_cli)
        base = saving_sep.join(parts) + self.suffix
        if os.getuid() == 0:
            return os.path.join(self.repo, base)
        else:
            # a more sophisticated approach would be to split name_from_cli
            # to find about another directory, but well..
            return base
        return base

    @staticmethod
    def bytes2human(n, format="{value:.3f} {symbol}"):
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
                return format.format(value=value, symbol=symbol)
        return format.format(symbol=symbols[0], value=n)

    def display(self, focus, verbose=False, sort_by='date', reverse=False, human_readable=True):
        # show available images in some sensible way
        #
        # focus: a list of re patterns; if empty, everything is displayed
        #        otherwise only files that match that name are displayed (with their symlinks though)
        # sort_by, reverse: how to sort
        # human_readable: boolean
        # 
        # rationale being that we sometimes use internal names, that are not really relevant
        # so here's one idea
        # (1) scan all files and gather them by clusters that point at the same file
        # for this we use a (inode, mtime) tuple, same value = same file
        # (2) for each cluster
        # there is exactly one real-file non-symlink (unless with hardlinks, where there can be several)
        # if there's at least one symlink then we don't show the real files at all
        # (3) show results
        print("==================== Images repository {}".format(self.repo))
        if focus:
            print("==== images matching any of", " ".join(focus))

        # gather one info per file, with
        # a key (inode, mtime, size) (same key = same file),
        # prefix (filename, no extension),
        # isalias
        # relevant (default is True and then can be turned off)
        infos = []
        for path in glob.glob("{}/*.ndz".format(self.repo)):
            prefix = os.path.basename(path).replace(".ndz", "")
            stat = os.stat(path)
            stat_tuple = (stat.st_size, stat.st_mtime, stat.st_ino, )
            info = {'stat_tuple': stat_tuple, 'prefix': prefix,
                    'isalias': os.path.islink(path), 'relevant':True}
            infos.append(info)
        # gather by same file (same stat_tuple)
        clusters = {}
        for info in infos:
            stat_tuple = info['stat_tuple']
            clusters.setdefault(stat_tuple, [])
            clusters[stat_tuple].append(info)

        # sort infos in one cluster, so that real files show up first
        sort_info = lambda info: info['isalias']
        for cluster in clusters.values():
            cluster.sort(key=sort_info)

        # sort clusters
        cluster_items = list(clusters.items())
        # k_v is a tuple with a key and a list of infos
        # k_v[0] is a key
        if sort_by == 'size':
            sort_clusters = lambda k_v: k_v[0][0]
        else:
            sort_clusters = lambda k_v: k_v[0][1]
        cluster_items.sort(key=sort_clusters, reverse=reverse)

        # is a given cluster filtered by focus
        def in_focus(cluster, focus):
            # an empty focus means to show all
            if not focus:
                return True
            # in this context a cluster is just a list of infos
            for info in cluster:
                for filter in focus:
                    # rough : no regexp, just find the filter or not
                    if info['prefix'].find(filter) >= 0:
#                        print("found match of {} in {}".
#                              format(filter, info['prefix']))
                        return True
                
        for stat_tuple, cluster in cluster_items:
            # skip the cluster if not in focus
            if not in_focus(cluster, focus):
                continue
            # do we have at least one symlink ?
            if not verbose:
                if any([info['isalias'] for info in cluster]):
                    for i in cluster:
                        if not i['isalias']:
                            i['relevant'] = False
            shown = False
            for info in cluster:
                if not info['relevant']:
                    continue
                (size, mtime, inode) = info['stat_tuple']
                print_size = size if not human_readable \
                             else ImagesRepo.bytes2human(size)
                date = time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))
                prefix = info['prefix']
                if not shown:
                    print("{:<18}{:>12}B {:40}".format(date, print_size, prefix))
                    shown = True
                else:
                    print("{:>31} {:40}".format("a.k.a.", prefix))


    # saving__fit16__2016-04-29@12-17__oai-gw-builds
    # saving==fit16==2016-04-21@16:15==oai_epc_b210
    def meaningful_part(self, filename):
        basename = os.path.basename(filename)
        basename = basename.replace(".ndz", "")
        sep = "[-_=]{2}"
        date = "[0-9]{4}-[0-9]{2}-[0-9]{2}@[0-9]{2}.[0-9]{2}"
        hostname = "fit[0-9]{2}"
        base_pattern = "saving" + sep + hostname + sep + date
        re_base = re.compile(base_pattern + "$" )
        named_pattern = base_pattern + sep + "(?P<meaningful>[\w\.-]+)"
        re_named = re.compile(named_pattern + "$" )
        match = re_named.match(basename)
        if match:
            return match.group('meaningful')
        match = re_base.match(basename)
        if match:
            return "anonymous"
        # otherwise return None
        

    def share(self, images, dest=None):
        """
        A utility to install one or several images in the shared repo

        parameters:
        * images: a list of image names (not searched in global repo) to transfer
        * dest: a name for the installed image - only if a single image is provided
        
        * multiple images
        destination name is based on origin name, 
        with the extra saving__*blabla removed

        * single image
        likewise, except if dest is provided

        * identity
        root is allowed to override destination, not regular users

        # return 0 if all goes well, 1 otherwise
        """

        ### check args
        if len(images) > 1 and dest:
            print("destination can be specified only with one image")
            return 1

        ### first pass
        # compute a list of tuples (origin, dest)
        moves = []
        if images:
            print("WARNING: share is an experimental feature reserved to root/sudo")
            r, e, s = os.getresuid()
            print("r={}, e={}, s={}".format(r, e, s))
            if r != 0 or e != 0 or s != 0:
                return
        for image in images:
            # should be s/t like this, but for now everyone looks like root
            # look_in_global = os.getlogin() == 'root'
            origin = self.locate_image(image, look_in_global=False)
            if not origin:
                print("Could not locate image {} - ignored"
                      .format(image))
                continue
            if dest:
                destination = dest
            else:
                meaningful = self.meaningful_part(origin)
                if not meaningful:
                    print("""Could not guess meaningful part in {}
give one with -d""".format(origin))
                    continue
                destination = meaningful
            destination = os.path.join(self.repo, destination + ".ndz")
            print("mv {} {}".format(origin, destination))
        
        return 0
