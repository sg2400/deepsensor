import deepsensor
from deepsensor.data.task import Task, concat_tasks
from deepsensor.model.convnp import ConvNP
from typing import Optional
import torch
from torch.cuda.amp import GradScaler


import numpy as np

import lab as B

from typing import List


def set_gpu_default_device() -> None:
    """Set default GPU device for the backend.

    Raises:
        RuntimeError
            If no GPU is available.
        RuntimeError
            If backend is not supported.
        NotImplementedError
            If backend is not supported.

    Returns:
        None.
    """
    if deepsensor.backend.str == "torch":
        # Run on GPU if available
        import torch

        if torch.cuda.is_available():
            # Set default GPU device
            torch.set_default_device("cuda")
            B.set_global_device("cuda:0")
        else:
            raise RuntimeError("No GPU available: torch.cuda.is_available() == False")
    elif deepsensor.backend.str == "tf":
        # Run on GPU if available
        import tensorflow as tf

        if tf.test.is_gpu_available():
            # Set default GPU device
            tf.config.set_visible_devices(
                tf.config.list_physical_devices("GPU")[0], "GPU"
            )
            B.set_global_device("GPU:0")
        else:
            raise RuntimeError("No GPU available: tf.test.is_gpu_available() == False")

    else:
        raise NotImplementedError(f"Backend {deepsensor.backend.str} not implemented")


def train_epoch(
    model: ConvNP,
    tasks: List[Task],
    lr: float = 5e-5,
    batch_size: int = None,
    opt=None,
    scaler=None,  # Pass scaler into train_epoch
    progress_bar=False,
    tqdm_notebook=False,
) -> List[float]:
    """Train model for one epoch.

    Args:
        model (:class:`~.model.convnp.ConvNP`):
            Model to train.
        tasks (List[:class:`~.data.task.Task`]):
            List of tasks to train on.
        lr (float, optional):
            Learning rate, by default 5e-5.
        batch_size (int, optional):
            Batch size. Defaults to None. If None, no batching is performed.
        opt (Optimizer, optional):
            TF or Torch optimizer. Defaults to None. If None,
            :class:`tensorflow:tensorflow.keras.optimizer.Adam` is used.
        scaler (bool, optional):
            Whether to use grad scaler for mixed precision to speed up training.
        progress_bar (bool, optional):
            Whether to display a progress bar. Defaults to False.
        tqdm_notebook (bool, optional):
            Whether to use a notebook progress bar. Defaults to False.

    Returns:
        List[float]: List of losses for each task/batch.
    """
    if deepsensor.backend.str == "tf":
        import tensorflow as tf

        if opt is None:
            opt = tf.keras.optimizers.Adam(lr)

        def train_step(tasks):
            if not isinstance(tasks, list):
                tasks = [tasks]
            with tf.GradientTape() as tape:
                task_losses = []
                for task in tasks:
                    task_losses.append(model.loss_fn(task, normalise=True))
                mean_batch_loss = B.mean(B.stack(*task_losses))
            grads = tape.gradient(mean_batch_loss, model.model.trainable_weights)
            opt.apply_gradients(zip(grads, model.model.trainable_weights))
            return mean_batch_loss

    elif deepsensor.backend.str == "torch":
        import torch.optim as optim

        if opt is None:
            opt = optim.Adam(model.model.parameters(), lr=lr)

        def train_step(tasks, opt, scaler=None):
            if not isinstance(tasks, list):
                tasks = [tasks]
            
            opt.zero_grad()  # Reset gradients
            
            task_losses = []

            # Make sure that tasks are properly batched
            for task in tasks:
                # Ensure each task is of a consistent shape
                task_loss = model.loss_fn(task, normalise=True)
                task_losses.append(task_loss)
        
            # Now stack the task losses into a batch and ensure that shapes align
            try:
                mean_batch_loss = B.mean(B.stack(*task_losses))  # Stack and compute mean loss
            except Exception as e:
                print(f"Error during stacking: {e}")
                print(f"Task losses shapes: {[t.shape for t in task_losses]}")
                raise
        
            # If AMP is being used, scale the loss and compute gradients with mixed precision
            if scaler:
                # Use mixed precision for the forward pass
                with torch.amp.autocast("cuda"):
                    mean_batch_loss = B.mean(B.stack(*task_losses))  # Recalculate loss in mixed precision
                scaler.scale(mean_batch_loss).backward()  # Backprop with scaling
                scaler.step(opt)  # Step optimizer
                scaler.update()  # Update scaler
            else:
                mean_batch_loss.backward()  # Standard backprop for full precision
                opt.step()  # Update weights
            
            return mean_batch_loss.detach().cpu().numpy()  # Return the loss value (detached)

        
    else:
        raise NotImplementedError(f"Backend {deepsensor.backend.str} not implemented")

    tasks = np.random.permutation(tasks)

    if batch_size is not None:
        n_batches = len(tasks) // batch_size  # Note that this will drop the remainder
    else:
        n_batches = len(tasks)

    if tqdm_notebook:
        from tqdm.notebook import tqdm
    else:
        from tqdm import tqdm

    batch_losses = []
    for batch_i in tqdm(range(n_batches), disable=not progress_bar):
        if batch_size is not None:
            task = concat_tasks(
                tasks[batch_i * batch_size : (batch_i + 1) * batch_size]
            )
        else:
            task = tasks[batch_i]
        batch_loss = train_step(task, opt, scaler)
        batch_losses.append(batch_loss)

    return batch_losses


class Trainer:
    """Class for training ConvNP models with an Adam optimiser.

    Args:
        lr (float): Learning rate
    """

    def __init__(self, model: ConvNP, lr: float = 5e-5):
        if deepsensor.backend.str == "tf":
            import tensorflow as tf

            self.opt = tf.keras.optimizers.Adam(lr)
        elif deepsensor.backend.str == "torch":
            import torch.optim as optim

            self.opt = optim.Adam(model.model.parameters(), lr=lr)

        self.model = model

    def __call__(
        self,
        tasks: List[Task],
        batch_size: int = None,
        scaler: Optional[GradScaler] = None,
        progress_bar=False,
        tqdm_notebook=False,
    ) -> List[float]:
        """Train model for one epoch."""
        return train_epoch(
            model=self.model,
            tasks=tasks,
            batch_size=batch_size,
            opt=self.opt,
            scaler=scaler,
            progress_bar=progress_bar,
            tqdm_notebook=tqdm_notebook,
        )
