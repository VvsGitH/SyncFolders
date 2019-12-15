import os
import shutil


class File:

    def __init__(self, name, path):
        self.name = name
        self.path = path

    def __str__(self):
        return f'{self.path}{self.name}'


class Folder:

    def __init__(self, path):
        self.path = path
        self.name = self.find_name()
        self.sub_folder_list = []
        self.file_list = []
        self.find_files()

    def __str__(self):
        return f'{self.path}'

    def find_name(self):
        post = self.path.rfind('/')
        pre = self.path.rfind('/', 0, post)
        return self.path[pre+1:post]

    def find_files(self):
        content_list = os.listdir(self.path)
        for content in content_list:
            if not os.path.isdir(self.path + content):
                self.file_list.append(File(content, self.path))
            else:
                self.sub_folder_list.append(Folder(f'{self.path}{content}/'))

    def get_file_list(self):
        file_list = []
        for file in self.file_list:
            file_list.append(str(file))
        return file_list

    def get_sub_folder_list(self):
        sub_list = []
        for sub_folder in self.sub_folder_list:
            sub_list.append(str(sub_folder))
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
