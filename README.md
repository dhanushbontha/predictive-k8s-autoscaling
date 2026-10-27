\# Predictive Kubernetes Autoscaling Using Load Forecasting



\## Overview



This project aims to compare traditional reactive autoscaling with predictive autoscaling in Kubernetes using workload forecasting.



The system will forecast incoming workload and evaluate whether scaling application replicas before demand increases can improve application responsiveness and resource utilization.



\## Objectives



\- Build a Python HTTP application that receives requests.

\- Deploy the application to Kubernetes using Minikube.

\- Configure Kubernetes Horizontal Pod Autoscaler (HPA) as the reactive baseline.

\- Develop a workload forecasting service using Prophet.

\- Integrate forecast-driven scaling using KEDA.

\- Run controlled load tests and compare both approaches.

\- Build a browser-based dashboard to visualize experiments.

\- Measure latency, forecast accuracy, scaling behavior, and resource utilization.



\## Technology Stack



\- Python

\- FastAPI

\- Docker

\- Kubernetes and Minikube

\- Kubernetes HPA

\- Prophet

\- KEDA

\- Locust

\- Git and GitHub



\## Project Structure



```text

predictive-k8s-autoscaling/

├── target-app/

├── backend/

├── forecasting/

├── frontend/

├── kubernetes/

├── experiments/

├── research/

├── tests/

├── .gitignore

└── README.md

```



\## Project Status



Initial setup. Implementation and testing are in progress.



\## Experimental Results



Results will be documented after controlled experiments have been executed. No experimental results are claimed at this stage.



\## Setup Instructions



Instructions for installation, deployment, and running experiments will be added as each component is implemented and verified.



