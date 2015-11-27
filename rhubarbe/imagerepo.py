from config import the_config
import os
import os.path
import glob
import time

class ImagesRepo:
    def __init__(self):
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
        * behaviour depends on actual id (root stores in globa repo, regular users store in '.')
        * name always contains nodename and date
        """
        parts = [nodename, time.strftime("%Y-%m-%d@%H:%M")]
        if name_from_cli:
            parts.append(name_from_cli)
        base = '='.join(parts) + self.suffix
        if os.getuid() == 0:
            return os.path.join(self.repo, base)
        else:
            # a more sophisticated approach would be to split name_from_cli
            # to find about another directory, but well..
            return base
        return base

    def display(self):
        # xxx make something nicer
        # show symlinks separately and defaults (ideally by number of links)
        # and then sort real images by size
        print("Contents of image directory {}".format(self.repo))
        for path in glob.glob("{}/*.ndz".format(self.repo)):
            file = path.replace(self.repo+"/", "").replace(self.repo, "")
            print(file)

the_imagesrepo = ImagesRepo()
