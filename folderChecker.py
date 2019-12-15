import os


def folder_checker(path):
    last = path.rfind('/')
    no_file_path = path[0:last]
    if not os.path.isdir(no_file_path):
        print('Cartella non trovata')
        os.mkdir(no_file_path)
    else:
        print('Cartella trovata')


path = './TestFolder/.OLD/NewFolder/test.txt'
folder_checker(path)
print(path)