import { createBrowserRouter, Navigate } from "react-router-dom";
import Login from "@pages/Login";
import Home from "@pages/Home";
import Layout from "@pages/Layout";
import Applications from "@pages/Applications";
import ApplicationMetrics from "@pages/ApplicationMetrics";
import ClusterMetrics from "@pages/ClusterMetrics";
import RegisteredApplications from "@pages/RegisteredApplications";
import Users from "@pages/Users";
import getRouterBasename from "@lib/router"; 

export const router = createBrowserRouter(
  [
    {
      path: "/login",
      element: <Login />,
    },
    {
      path: "/",
      element: <Layout />,
      children: [
        { index: true, element: <Home /> },
        { path: "apps/:tierIndex", element: <Applications /> },
        { path: "apps/:tierIndex/:appName/metrics", element: <ApplicationMetrics /> },
        { path: "metrics/:tierIndex/:view?", element: <ClusterMetrics /> },
        { path: "registered-applications", element: <RegisteredApplications /> },
        { path: "users", element: <Users /> },
      ],
    },
    {
      path: "*",
      element: <Navigate to="/" replace />,
    },
  ],
  { basename: getRouterBasename() }
);
