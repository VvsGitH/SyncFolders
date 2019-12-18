import os
import shutil


class File:

    def __init__(self, path, name):
        self.name = name
        self.path = path
        self.stats = os.stat(path)

    def __str__(self):
        return f'{self.path}'


class Folder:

    def __init__(self, path, name=''):
        self.path = path
        if name == 'None':
            self.name = self.find_name()
        else:
            self.name = name
        self.sub_folder_dict = {}
        self.sub_folder_keys = []
        self.file_dict = {}
        self.file_keys = []
        self.search_content()

    def __str__(self):
        return f'{self.path}'

    def find_name(self):
        post = self.path.rfind('/')
        pre = self.path.rfind('/', 0, post)
        return self.path[pre+1:post]

    def search_content(self):
        content_list = os.listdir(self.path)
        for content in content_list:
            if not os.path.isdir(self.path + content):
                self.file_dict[content] = File(f'{self.path}{content}', content)
                self.file_keys.append(content)
            else:
                self.sub_folder_dict[content] = Folder(f'{self.path}{content}/', content)
                self.sub_folder_keys.append(content)

    def get_file_list(self):
        file_list = []
        for file in self.file_keys:
            file_list.append(str(self.file_dict[file]))
        return file_list

    def get_sub_folder_list(self):
        sub_list = []
        for sub_folder in self.sub_folder_keys:
            sub_list.append(str(self.sub_folder_dict[sub_folder]))
        return sub_list


def folder_checker(path):
    last = path.rfind('/')
    no_file_path = path[0:last]
    if not os.path.isdir(no_file_path):
        print('Cartella non trovata')
        os.mkdir(no_file_path)
    else:
        print('Cartella trovata')


PATH = './TestFolder/'
sourceFolder = Folder(PATH)
print(sourceFolder.get_sub_folder_list())
print(sourceFolder.get_file_list())
# print last modify date
print(sourceFolder.file_dict[sourceFolder.file_keys[0]].stats.st_mtime)
