import os
import shutil
import time


def display_file_stats(filename):
    file_stats = os.stat(os.path.basename(filename))
    print('\tMode       :', file_stats.st_mode)
    print('\tCreated    :', time.ctime(file_stats.st_ctime))
    print('\tAccessed   :', time.ctime(file_stats.st_atime))
    print('\tModified   :', time.ctime(file_stats.st_mtime))


# os.mkdir('TestFolder') create folder
display_file_stats('TestFile.txt')

shutil.copy2('TestFile.txt', 'TestFolder/TestFile.txt')
display_file_stats('TestFolder/TestFile.txt')
