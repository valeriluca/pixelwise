from app.classifier import classify_batch
from sklearn.datasets import fetch_openml
import numpy as np

print("started")
X, y = fetch_openml("mnist_784", version=1, 
    return_X_y=True, as_frame=False)
print("fetch_openml has run")
images = X[:5].reshape(-1, 28, 28).astype(np.uint8)
print("image reshape done")
truth = y[:5]
print("truth done")
results = classify_batch(images)
print("classifybatch(images) done")
for r, t in zip(results, truth):
    print(f"Pred: {r['prediction']} "
        f"(conf {r['confidence']:.2f}) True: {t}")
