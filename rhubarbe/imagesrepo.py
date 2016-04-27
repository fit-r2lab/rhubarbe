import os
import os.path
import glob
import time

from rhubarbe.config import Config
from rhubarbe.singleton import Singleton

class ImagesRepo(metaclass = Singleton):
    def __init__(self):
        the_config = Config()
        self.repo = the_config.value('frisbee', 'images_dir')
        self.name = the_config.value('frisbee', 'default_image')

    suffix = ".ndz"

    def default(self):
        return os.path.join(self.repo, self.name)

    def add_extension(self, file):
        for f in (file, file + self.suffix):
            if os.path.exists(f):
                return f

    def locate(self, image):
        return \
          self.add_extension(image) \
           if os.path.isabs(image) \
           else self.add_extension(os.path.join(self.repo, image))

    def where_to_save(self, nodename, name_from_cli):
        """
        given a nodename, plus an option user-provided image name (may be None)
        computes the actual path where to store an image
        * behaviour depends on actual id (root stores in global repo, regular users store in '.')
        * name always contains nodename and date
        """
        parts = ['saving', nodename, time.strftime("%Y-%m-%d@%H:%M")]
        if name_from_cli:
            parts.append(name_from_cli)
        base = '=='.join(parts) + self.suffix
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

    def display(self, sort_by='date', reverse=False, human_readable=True):
        # try to show available images in some sensible way
        # rationale being that we sometimes use internal names, that are not really relevant
        # so here's one idea
        # (1) scan all files and gather them by clusters that point at the same file
        # for this we use a (inode, mtime) tuple, same value = same file
        # (2) for each cluster
        # there is exactly one real-file non-symlink (unless with hardlinks, where there can be several)
        # if there's at least one symlink then we don't show the real files at all
        # (3) show results
        print("==================== Images repository {}".format(self.repo))

        # gather one info per file, with
        # a key (inode, mtime, size) (same key = same file),
        # prefix (filename, no extension),
        # isalias
        # relevant (default is True and then can be turned off)
        infos = []
        for path in glob.glob("{}/*.ndz".format(self.repo)):
            prefix = os.path.basename(path).replace(".ndz", "")
            stat = os.stat(path)
            key = (stat.st_size, stat.st_mtime, stat.st_ino, )
            info = {'key': key, 'prefix': prefix, 'isalias': os.path.islink(path), 'relevant':True}
            infos.append(info)
        # gather by same file (same key)
        clusters = {}
        for info in infos:
            key = info['key']
            clusters.setdefault(key, [])
            clusters[key].append(info)
        # sort clusters
        cluster_items = list(clusters.items())
        # k_v is a tuple with a key and a list of infos
        # k_v[0] is a key
        if sort_by == 'size':
            key_function = key=lambda k_v: k_v[0][0]
        else:
            key_function = key=lambda k_v: k_v[0][1]
        cluster_items.sort(key=key_function, reverse=reverse)
        for key, cluster in cluster_items:
            # do we have at least one symlink ?
            if any([info['isalias'] for info in cluster]):
                for i in cluster:
                    if not i['isalias']:
                        i['relevant'] = False
            shown = False
            for info in cluster:
                if not info['relevant']:
                    continue
                (size, mtime, inode) = info['key']
                print_size = size if not human_readable \
                             else ImagesRepo.bytes2human(size)
                date = time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))
                prefix = info['prefix']
                if not shown:
                    print("{:<18}{:>12}B {:40}".format(date, print_size, prefix))
                    shown = True
                else:
                    print("{:>31} {:40}".format("a.k.a.", prefix))
