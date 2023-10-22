import sys
import time
import torch
import argparse
from mpi4py import MPI
from appfl.config import *
from appfl.misc.data import *
from losses.utils import get_loss
from models.utils import get_model
from metric.utils import get_metric
import appfl.run_grpc_server as grpc_server
import appfl.run_grpc_client as grpc_client
from dataloader.cifar10_dataloader import get_cifar10

## read arguments  
parser = argparse.ArgumentParser() 
parser.add_argument('--device', type=str, default="cpu")    

## dataset and model
parser.add_argument('--dataset', type=str, default="CIFAR10")   
parser.add_argument('--num_channel', type=int, default=3)   
parser.add_argument('--num_classes', type=int, default=10)   
parser.add_argument('--num_pixel', type=int, default=32)   
parser.add_argument('--model', type=str, default="resnet18")   
parser.add_argument('--train_data_batch_size', type=int, default=128)   
parser.add_argument('--test_data_batch_size', type=int, default=128)  
parser.add_argument("--partition", type=str, default="iid", choices=["iid", "class_noiid", "dirichlet_noiid"])
parser.add_argument("--seed", type=int, default=42) 

## clients
parser.add_argument('--client_optimizer', type=str, default="Adam")    
parser.add_argument('--client_lr', type=float, default=1e-3)    
parser.add_argument("--local_train_pattern", type=str, default="steps", choices=["steps", "epochs"], help="For local optimizer, what counter to use, number of steps or number of epochs")
parser.add_argument("--num_local_steps", type=int, default=10)
parser.add_argument("--num_local_epochs", type=int, default=1)
parser.add_argument("--do_validation", action="store_true")

## server
parser.add_argument('--server', type=str, default="ServerFedAvg")    
parser.add_argument('--num_epochs', type=int, default=1)    
parser.add_argument("--server_lr", type=float, default=0.01)
parser.add_argument("--mparam_1", type=float, default=0.9)
parser.add_argument("--mparam_2", type=float, default=0.99)
parser.add_argument("--adapt_param", type=float, default=0.001) 

## privacy preserving
parser.add_argument("--use_dp", action="store_true", default=False, help="Whether to enable differential privacy technique to preserve privacy")
parser.add_argument("--epsilon", type=float, default=1, help="Privacy budget - stronger privacy as epsilon decreases")
parser.add_argument("--clip_grad", action="store_true", default=False, help="Whether to clip the gradients")
parser.add_argument("--clip_value", type=float, default=1.0, help="Max norm of the gradients")
parser.add_argument("--clip_norm", type=float, default=1, help="Type of the used p-norm for gradient clipping")
 
## loss function
parser.add_argument("--loss_fn", type=str, required=False, help="path to the custom loss function definition file, use cross-entropy loss by default if no path is specified")
parser.add_argument("--loss_fn_name", type=str, required=False, help="class name for the custom loss in the loss function definition file, choose the first class by default if no name is specified")

args = parser.parse_args()    

if torch.cuda.is_available():
    args.device="cuda"

def main():
    # logging.basicConfig(stream=sys.stdout, level=logging.INFO)
    comm = MPI.COMM_WORLD
    comm_rank = comm.Get_rank()
    comm_size = comm.Get_size()
    assert comm_size > 1, "This script requires the toal number of processes to be greater than one!"
    args.num_clients = comm_size - 1 

    cfg = OmegaConf.structured(Config)
    cfg.device = args.device 
    cfg.reproduce = True
    if cfg.reproduce == True:
        set_seed(1)

    ## dataset
    cfg.train_data_batch_size = args.train_data_batch_size
    cfg.test_data_batch_size = args.test_data_batch_size
    cfg.train_data_shuffle = True

    ## clients
    cfg.num_clients = args.num_clients
    cfg.fed.clientname = "ClientOptim" if args.local_train_pattern == "epochs" else "ClientStepOptim"
    cfg.fed.args.optim = args.client_optimizer
    cfg.fed.args.optim_args.lr = args.client_lr
    cfg.fed.args.num_local_steps = args.num_local_steps
    cfg.fed.args.num_local_epochs = args.num_local_epochs
    cfg.validation = args.do_validation
    
    ## server
    cfg.fed.servername = args.server
    cfg.num_epochs = args.num_epochs

    ## outputs        
    cfg.output_dirname = f"./outputs_{args.dataset}_{args.partition}_{args.num_clients}clients_{args.server}_{args.num_epochs}epochs_grpc"

    ## adaptive server
    cfg.fed.args.server_learning_rate = args.server_lr          # FedAdam
    cfg.fed.args.server_adapt_param = args.adapt_param          # FedAdam
    cfg.fed.args.server_momentum_param_1 = args.mparam_1        # FedAdam, FedAvgm
    cfg.fed.args.server_momentum_param_2 = args.mparam_2        # FedAdam  

    ## privacy preserving
    cfg.fed.args.use_dp = args.use_dp
    cfg.fed.args.epsilon = args.epsilon
    cfg.fed.args.clip_grad = args.clip_grad
    cfg.fed.args.clip_value = args.clip_value
    cfg.fed.args.clip_norm = args.clip_norm

    start_time = time.time()

    ## User-defined model
    model = get_model(args)
    loss_fn = get_loss(args.loss_fn, args.loss_fn_name)
    metric = get_metric(args.metric, args.metric_name)

    ## User-defined data
    train_datasets, test_dataset = get_cifar10(comm, cfg, partition=args.partition, visualization=True, seed=args.seed, alpha1=args.num_clients)


    print("----------Loaded Datasets and Model----------Elapsed Time=", time.time() - start_time)

    if comm_size > 1:
        # Try to launch both a server and clients.
        if comm_rank == 0:            
            grpc_server.run_server(cfg, model, loss_fn, cfg.num_clients, test_dataset, metric)
        else:            
            grpc_client.run_client(cfg, comm_rank-1, model, loss_fn, train_datasets[comm_rank - 1], comm_rank, test_dataset, metric)
            
        print("------DONE------", comm_rank)
    else:
        # Just launch a server.
        grpc_server.run_server(cfg, model, cfg.num_clients, test_dataset)

if __name__ == "__main__":
    main()