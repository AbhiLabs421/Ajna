import React from 'react';

const Home = () => (
  <div className="container mt-5">
    <div className="jumbotron text-center bg-light p-5 rounded">
      <h1 className="display-4">Welcome to CCIL AI Platform</h1>
      <p className="lead">Empowering intelligent conversations with cutting‑edge AI.</p>
      <hr className="my-4" />
      <p>Explore features, try the demo, or get started now.</p>
      <a className="btn btn-primary btn-lg" href="/Chat" role="button">
        Try Demo
      </a>
    </div>
  </div>
);

export default Home;
