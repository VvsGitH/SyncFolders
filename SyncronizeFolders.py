import os
import shutil


def root_extractor(path):
    root = []
    file_list = os.listdir(path)
    for file in file_list:
        if not os.path.isdir(path + file):
            root.append(path + file)
        else:
            sub_root = root_extractor(f'{path}{file}/')
            for sub_file in sub_root:
                root.append(sub_file)
    return root


def path_remover(fold_path, root):
    new_root = ['']*len(root)
    for ind, path in enumerate(root):
        new_root[ind] = path.replace(fold_path, '')
    return new_root


def file_checker(master_root, slave_root):
    ms_index_vector = ['']*len(master_root)
    for ind, file in enumerate(master_root):
        ms_index_vector[ind] = slave_root.index(file) if (file in slave_root) else 'np'
    sm_index_vector = ['']*len(slave_root)
    for ind, file in enumerate(slave_root):
        sm_index_vector[ind] = master_root.index(file) if (file in master_root) else 'np'
    return ms_index_vector, sm_index_vector


def folder_checker(path):
    last = path.rfind('/')
    no_file_path = path[0:last]
    if not os.path.isdir(no_file_path):
        os.makedirs(no_file_path)


def last_modify(file_path):
    file_stats = os.stat(file_path)
    return file_stats.st_mtime


# INSERTING SOURCE AND DESTINATION FOLDERS NAMES
source = './TestFolder/'
destination = './TestFolder2/'

# EXTRACTING FILE LIST FROM FOLDERS
folder_root1 = root_extractor(source)
folder_root2 = root_extractor(destination)
print(f'''extracting file_list from folders:
source files:      {folder_root1}
destination files: {folder_root2}''')

# REMOVING FOLDER MAIN PATH FROM FILES' NAMES - In this way source and destination could have different names
file_list_source = path_remover(source, folder_root1)
file_list_destination = path_remover(destination, folder_root2)
print(f'''removing root_folder path from file paths:
source files:      {file_list_source}
destination files: {file_list_destination}''')

# COMPARING SOURCE AND DESTINATION FOR EQUALLY NAMED FILES
sd_match, ds_match = file_checker(file_list_source, file_list_destination)
print(f'''source to destination match_index: {sd_match}
destination to source match_index: {ds_match}''')

# COPYING UNMATCHED DESTINATION FILES INTO .OLD IN SOURCE FOLDER
for i, elm in enumerate(ds_match):
    if elm == 'np':
        source_file = folder_root2[i]
        destination_location = source + '.OLD/' + file_list_destination[i]
        folder_checker(destination_location)
        print(f'moving: {source_file} to: {destination_location}')
        shutil.move(source_file, destination_location)
else:
    print('no unmatched files in destination folder')

# COPYING UNMATCHED SOURCE FILES INTO DESTINATION FOLDER
for i, elm in enumerate(sd_match):
    if elm == 'np':
        source_file = folder_root1[i]
        destination_location = destination + file_list_source[i]
        folder_checker(destination_location)
        print(f'copying: {source_file} to: {destination_location}')
        shutil.copy2(source_file, destination_location)
else:
    print('no unmatched files in source folder')

# CHECK LAST MODIFIED TIME FOR MATCHING FILES AND OVERWRITE OLD FILES
for i, elm in enumerate(sd_match):
    if elm != 'np':
        source_mtime = last_modify(folder_root1[i])
        destination_mtime = last_modify(folder_root2[int(elm)])
        if source_mtime > destination_mtime:
            print(f'newer file found in source folder: {folder_root1[i]}')
            shutil.copy2(folder_root1[i], folder_root2[int(elm)])
        elif destination_mtime > source_mtime:
            print(f'newer file found in destination folder: {folder_root1[i]}')
            shutil.copy2(folder_root1[i], folder_root2[int(elm)])
else:
    print('no old files in destination or source folder')




