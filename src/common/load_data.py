import cv2
import keras
import numpy as np
import os
import pandas as pd
from sklearn.model_selection import train_test_split
import tarfile
import tqdm

DATA_DIR = '../../data/'

# http://www.cs.columbia.edu/CAVE/databases/pubfig/download/lfw_attributes.txt
ATTRS_NAME = DATA_DIR + 'lfw/lfw_attributes.txt'

# http://vis-www.cs.umass.edu/lfw/lfw-deepfunneled.tgz
# noinspection SpellCheckingInspection
IMAGES_NAME = DATA_DIR + 'lfw/lfw-deepfunneled.tgz'

# http://vis-www.cs.umass.edu/lfw/lfw.tgz
RAW_IMAGES_NAME = DATA_DIR + 'lfw/lfw.tgz'


# noinspection PyUnresolvedReferences
def decode_image_from_raw_bytes(raw_bytes):
    img = cv2.imdecode(np.asarray(bytearray(raw_bytes), dtype=np.uint8), 1)
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    return img


def load_cifar10_dataset():
    (x_train, y_train), (x_test, y_test) = keras.datasets.cifar10.load_data()

    x_train = x_train / 255. - 0.5
    x_test = x_test / 255. - 0.5

    y_train = keras.utils.to_categorical(y_train)
    y_test = keras.utils.to_categorical(y_test)

    return x_train, y_train, x_test, y_test


def load_faces_dataset():
    x, attr = load_lfw_dataset(use_raw=True, dimx=32, dimy=32)
    img_shape = x.shape[1:]

    # center images
    x = x.astype('float32') / 255. - 0.5

    # split
    x_train, x_test = train_test_split(x, test_size=0.1, random_state=42)
    return img_shape, attr, x_train, x_test


# noinspection SpellCheckingInspection,PyUnresolvedReferences
def load_lfw_dataset(use_raw=False, dx=80, dy=80, dimx=45, dimy=45):
    """
    Labeled Faces in the Wild is a database of face photographs designed for studying the
    problem of unconstrained face recognition. The data set contains more than 13,000 images
    of faces collected from the web. Each face has been labeled with the name of the person
    pictured. 1680 of the people pictured have two or more distinct photos in the data set.

    :param use_raw:
    :param dx:
    :param dy:
    :param dimx:
    :param dimy:
    :return:
    """
    # read attrs
    df_attrs = pd.read_csv(ATTRS_NAME, sep='\t', skiprows=1)
    df_attrs = pd.DataFrame(df_attrs.iloc[:, :-1].values, columns=df_attrs.columns[1:])
    imgs_with_attrs = set(map(tuple, df_attrs[['person', 'imagenum']].values))

    # read photos
    all_photos = []
    photo_ids = []

    with tarfile.open(RAW_IMAGES_NAME if use_raw else IMAGES_NAME) as f:
        for m in tqdm.tqdm_notebook(f.getmembers()):
            if m.isfile() and m.name.endswith('.jpg'):
                # prepare image
                img = decode_image_from_raw_bytes(f.extractfile(m).read())
                img = img[dy:-dy, dx:-dx]
                img = cv2.resize(img, (dimx, dimy))

                # parse person
                fname = os.path.split(m.name)[-1]
                fname_split = fname[:-4].replace('_', ' ').split()
                person_id = ' '.join(fname_split[:-1])
                photo_number = int(fname_split[-1])
                if (person_id, photo_number) in imgs_with_attrs:
                    all_photos.append(img)
                    photo_ids.append({'person': person_id, 'imagenum': photo_number})

    photo_ids = pd.DataFrame(photo_ids)
    all_photos = np.stack(all_photos).astype('uint8')

    # preserve photo_ids order
    all_attrs = photo_ids.merge(df_attrs, on=('person', 'imagenum')).drop(['person', 'imagenum'], axis=1)
    return all_photos, all_attrs


def load_mnist_dataset(flatten=False):
    """
    MNIST database of handwritten digits.

    Dataset of 60,000 28x28 grayscale images of the 10 digits, along with a test set of 10,000 images.

    :param flatten: boolean setting to flatten pixel matrix to vector
    :return: dataset divided into features and labels for training, validation and test
    """
    # loads into ~/.keras/datasets
    (x_train, y_train), (x_test, y_test) = keras.datasets.mnist.load_data()

    # normalize X
    x_train = x_train.astype('float32') / 255.
    x_test = x_test.astype('float32') / 255.

    # we reserve the last 10000 training examples for validation
    x_train, x_val = x_train[:-10000], x_train[-10000:]
    y_train, y_val = y_train[:-10000], y_train[-10000:]

    if flatten:
        x_train = x_train.reshape([x_train.shape[0], -1])
        x_val = x_val.reshape([x_val.shape[0], -1])
        x_test = x_test.reshape([x_test.shape[0], -1])

    return x_train, y_train, x_val, y_val, x_test, y_test


def load_names():
    """
    The dataset contains around 8,000 names from different cultures,
    all in latin transcript.

    :return:
    """
    with open('../../../data/names.txt') as f:
        names = f.read()[:-1].split('\n')
        return [' ' + name for name in names]
