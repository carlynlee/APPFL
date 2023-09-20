import os
import time

import json
import numpy as np
import torch

from appfl.config import *
from appfl.misc.data import *
from models.cnn import *
import appfl.run_serial as rs
import appfl.run_mpi as rm
from mpi4py import MPI
from models.utils import get_model
import argparse

from PIL import Image
from PIL import ImageOps

""" read arguments """

parser = argparse.ArgumentParser()

parser.add_argument("--device", type=str, default="cpu")

## dataset
parser.add_argument("--dataset", type=str, default="CELEBA")
parser.add_argument("--num_channel", type=int, default=3)
parser.add_argument("--num_classes", type=int, default=2)
parser.add_argument("--num_pixel", type=int, default=218)
parser.add_argument("--model", type=str, default="CNN")

## clients
parser.add_argument("--num_clients", type=int, default=1)
parser.add_argument("--client_optimizer", type=str, default="Adam")
parser.add_argument("--client_lr", type=float, default=1e-3)
parser.add_argument("--num_local_epochs", type=int, default=1)

## server
parser.add_argument("--server", type=str, default="ServerFedAvg")
parser.add_argument("--num_epochs", type=int, default=2)

parser.add_argument("--server_lr", type=float, required=False)
parser.add_argument("--mparam_1", type=float, required=False)
parser.add_argument("--mparam_2", type=float, required=False)
parser.add_argument("--adapt_param", type=float, required=False)

parser.add_argument("--pretrained", type=int, default=0)

args = parser.parse_args()

if torch.cuda.is_available():
    args.device = "cuda"


dir = os.getcwd() + "/datasets/RawData/%s" % (args.dataset)

def get_data(comm: MPI.Comm):
    # test data for a server 
    test_data_raw = {}
    test_data_input = []
    test_data_label = []
    imagedir = dir + "/raw/img_align_celeba/"
    with open("%s/test/all_data_niid_05_keep_0_test_9.json" % (dir)) as f:
        test_data_raw = json.load(f)
    for client in test_data_raw["users"]:
        for imagename in test_data_raw["user_data"][client]["x"]:            
            imagepath = imagedir+imagename        
            img = Image.open(imagepath)
            img = ImageOps.pad(img, [args.num_pixel,args.num_pixel])
            rgb = img.convert('RGB')            
            arr = np.asarray(rgb).copy()
            arr = np.moveaxis(arr, -1, 0)   
            arr = arr / 255  # scale all pixel values to between 0 and 1            
            test_data_input.append(arr)
        for data_label in test_data_raw["user_data"][client]["y"]:
            test_data_label.append(data_label)
    test_dataset = Dataset(
        torch.FloatTensor(test_data_input), torch.LongTensor(test_data_label)
    )

    # training data for multiple clients
    train_data_raw = {}
    train_datasets = []
    with open("%s/train/all_data_niid_05_keep_0_train_9.json" % (dir)) as f:
        train_data_raw = json.load(f)

    for client in train_data_raw["users"]:

        train_data_input_resize = []
        for data_input in train_data_raw["user_data"][client]["x"]:
            imagepath = imagedir+imagename        
            img = Image.open(imagepath)
            img = ImageOps.pad(img, [args.num_pixel,args.num_pixel])
            rgb = img.convert('RGB')        
            arr = np.asarray(rgb).copy()
            arr = np.moveaxis(arr, -1, 0)        
            arr = arr / 255  # scale all pixel values to between 0 and 1
            train_data_input_resize.append(arr)
        train_datasets.append(
            Dataset(
                torch.FloatTensor(train_data_input_resize),
                torch.LongTensor(train_data_raw["user_data"][client]["y"]),
            )
        )
    
    return train_datasets, test_dataset


def main():    

    comm = MPI.COMM_WORLD
    comm_rank = comm.Get_rank()
    comm_size = comm.Get_size()

    # read default configuration
    cfg = OmegaConf.structured(Config)

    ## Reproducibility
    torch.manual_seed(1)
    torch.backends.cudnn.deterministic = True

    start_time = time.time()
    train_datasets, test_dataset = get_data(comm)

    if cfg.data_sanity == True:
        data_sanity_check(
            train_datasets, test_dataset, args.num_channel, args.num_pixel
        )

    args.num_clients = len(train_datasets)

    model = get_model(args)
    loss_fn = torch.nn.CrossEntropyLoss()   
    print(
        "----------Loaded Datasets and Model----------Elapsed Time=",
        time.time() - start_time,
    )
    
    cfg.device = args.device  

    ## clients
    cfg.num_clients = args.num_clients
    cfg.fed.args.optim = args.client_optimizer
    cfg.fed.args.optim_args.lr = args.client_lr
    cfg.fed.args.num_local_epochs = args.num_local_epochs

    ## server
    cfg.fed.servername = args.server
    cfg.num_epochs = args.num_epochs

    ## outputs
    cfg.use_tensorboard = True

    if comm_size > 1:
        if comm_rank == 0:
            rm.run_server(cfg, comm, model, loss_fn, args.num_clients, test_dataset, args.dataset)
        else:
            rm.run_client(cfg, comm, model, loss_fn, args.num_clients, train_datasets)
        print("------DONE------", comm_rank)
    else:
        rs.run_serial(cfg, model, loss_fn, train_datasets, test_dataset, args.dataset)


if __name__ == "__main__": 
    main()

# To run CUDA-aware MPI:
# mpiexec -np 2 --mca opal_cuda_support 1 python ./celeba.py
# To run MPI:
# mpiexec -np 2 python ./celeba.py
# To run:
# python ./celeba.py
# To run with resnet pretrained weight:
# python ./celeba.py --model resnet18 --pretrained 1