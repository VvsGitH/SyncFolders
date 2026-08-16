import os


def root_extractor(path):
    folder_list = []
    file_list = os.listdir(path)
    for file in file_list:
        if not os.path.isdir(file):
            folder_list.append(path + file)
        else:
            folder_list.append(root_extractor(f'{path}{file}/'))
    return folder_list


current_path = './'
root = root_extractor(current_path)
print(root)
print(root[0][1])
