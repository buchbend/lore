function greet() {
  return helper();
}

function helper() {
  return 1;
}

class Widget {
  render() {
    return greet();
  }
}
